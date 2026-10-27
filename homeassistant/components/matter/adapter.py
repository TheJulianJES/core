"""Matter to Home Assistant adapter."""

from typing import TYPE_CHECKING, cast

from chip.clusters import Objects as clusters
from matter_server.client.models.device_types import BridgedNode
from matter_server.common.models import EventType, ServerInfoMessage

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED, UndefinedType

from .const import DOMAIN, ID_TYPE_DEVICE_ID, ID_TYPE_SERIAL, LOGGER
from .discovery import async_discover_entities
from .helpers import (
    MatterConfigEntry,
    get_device_endpoint,
    get_device_id,
    get_discovery_key,
    get_entity_unique_id,
    get_legacy_device_id,
)

if TYPE_CHECKING:
    from matter_server.client import MatterClient
    from matter_server.client.models.node import MatterEndpoint, MatterNode

    from .models import MatterEntityInfo


def get_clean_name(name: str | None) -> str | None:
    """Strip spaces and null char from the name."""
    if name is None:
        return name
    name = name.replace("\x00", "")
    return name.strip() or None


class MatterAdapter:
    """Connect Matter into Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        matter_client: MatterClient,
        config_entry: MatterConfigEntry,
    ) -> None:
        """Initialize the adapter."""
        self.matter_client = matter_client
        self.hass = hass
        self.config_entry = config_entry
        self.platform_handlers: dict[Platform, AddEntitiesCallback] = {}
        self.discovered_entities: set[str] = set()

    def register_platform_handler(
        self, platform: Platform, add_entities: AddEntitiesCallback
    ) -> None:
        """Register a platform handler."""
        self.platform_handlers[platform] = add_entities

    async def setup_nodes(self) -> None:
        """Set up all existing nodes and subscribe to new nodes."""
        nodes = self.matter_client.get_nodes()
        self._split_merged_devices(nodes)
        for node in nodes:
            self._setup_node(node)

        def node_added_callback(event: EventType, node: MatterNode) -> None:
            """Handle node added event."""
            self._setup_node(node)

        def node_updated_callback(event: EventType, node: MatterNode) -> None:
            """Handle node updated event."""
            if not node.available:
                return
            # We always run the discovery logic again,
            # because the firmware version could have been changed or features added.
            self._setup_node(node)

        def endpoint_added_callback(event: EventType, data: dict[str, int]) -> None:
            """Handle endpoint added event."""
            node = self.matter_client.get_node(data["node_id"])
            endpoint = node.endpoints[data["endpoint_id"]]
            # Ensure the bridge device (endpoint 0) is registered before a
            # bridged child endpoint resolves it as its via_device.
            device_endpoint = get_device_endpoint(endpoint)
            if (
                device_endpoint.is_bridged_device
                and node.endpoints[0] != device_endpoint
            ):
                self._setup_endpoint(node.endpoints[0])
            self._setup_endpoint(endpoint)

        def endpoint_removed_callback(event: EventType, data: dict[str, int]) -> None:
            """Handle endpoint removed event."""
            server_info = cast(ServerInfoMessage, self.matter_client.server_info)
            try:
                node = self.matter_client.get_node(data["node_id"])
            except KeyError:
                return  # race condition
            device_registry = dr.async_get(self.hass)
            endpoint = node.endpoints.get(data["endpoint_id"])
            if not endpoint:
                return  # race condition
            if get_device_endpoint(endpoint) != endpoint:
                # A composed device is represented by a single HA device, which is
                # only removed once its compose parent endpoint is removed.
                return
            node_device_id = get_device_id(server_info, endpoint)
            identifier = (DOMAIN, f"{ID_TYPE_DEVICE_ID}_{node_device_id}")
            if device := device_registry.async_get_device_by_identifier(
                identifier, self.config_entry.entry_id
            ):
                device_registry.async_remove_device(device.id)

        def node_removed_callback(event: EventType, node_id: int) -> None:
            """Handle node removed event."""
            try:
                node = self.matter_client.get_node(node_id)
            except KeyError:
                return  # race condition
            for endpoint_id in node.endpoints:
                endpoint_removed_callback(
                    EventType.ENDPOINT_REMOVED,
                    {"node_id": node_id, "endpoint_id": endpoint_id},
                )

        self.config_entry.async_on_unload(
            self.matter_client.subscribe_events(
                callback=endpoint_added_callback, event_filter=EventType.ENDPOINT_ADDED
            )
        )
        self.config_entry.async_on_unload(
            self.matter_client.subscribe_events(
                callback=endpoint_removed_callback,
                event_filter=EventType.ENDPOINT_REMOVED,
            )
        )
        self.config_entry.async_on_unload(
            self.matter_client.subscribe_events(
                callback=node_removed_callback, event_filter=EventType.NODE_REMOVED
            )
        )
        self.config_entry.async_on_unload(
            self.matter_client.subscribe_events(
                callback=node_added_callback, event_filter=EventType.NODE_ADDED
            )
        )
        self.config_entry.async_on_unload(
            self.matter_client.subscribe_events(
                callback=node_updated_callback, event_filter=EventType.NODE_UPDATED
            )
        )

    def _split_merged_devices(self, nodes: list[MatterNode]) -> None:
        """Split device entries that were merged through a shared serial number.

        The serial number used to be an additional identifier, which merged every
        node reporting the same serial number into a single device entry, e.g. the
        old and the new node of a re-commissioned device. Give each device ID its own
        entry again, together with its entities, and drop the serial number
        identifiers.
        """
        server_info = cast(ServerInfoMessage, self.matter_client.server_info)
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        device_id_prefix = f"{ID_TYPE_DEVICE_ID}_"
        serial_prefix = f"{ID_TYPE_SERIAL}_"
        fabric_id_hex = f"{server_info.compressed_fabric_id:016X}"
        nodes_by_id = {node.node_id: node for node in nodes}

        def owner_rank(device_id: str) -> tuple[bool, bool, bool, int, bool, str]:
            """Rank which device ID of a merged entry keeps the entry."""
            # device ID format: <fabric ID>-<node ID>-<MatterNodeDevice|endpoint ID>
            parts = device_id.split("-", 2)
            try:
                node_id = int(parts[1], 16)
            except IndexError, ValueError:
                node_id = -1
            node = nodes_by_id.get(node_id) if parts[0] == fabric_id_hex else None
            is_node_device = parts[-1] == "MatterNodeDevice"
            # a bridge can replace the endpoint of a bridged device by a new one
            endpoint_exists = node is not None and (
                is_node_device
                or (parts[-1].isdigit() and int(parts[-1]) in node.endpoints)
            )
            # node IDs are allocated in increasing order, so the highest one is
            # normally the most recently commissioned node
            return (
                node is not None and node.available,
                node is not None,
                endpoint_exists,
                node_id,
                is_node_device,
                device_id,
            )

        def entity_owner(unique_id: str, device_ids: set[str]) -> str | None:
            """Return the device ID an entity unique ID belongs to."""
            matches = [d for d in device_ids if unique_id.startswith(f"{d}-")]
            if not matches:
                # unique IDs from before matter-python-client 1.5.0 can use the
                # device ID of another endpoint of the same node
                matches = [
                    d
                    for d in device_ids
                    if unique_id.startswith(f"{d.rsplit('-', 1)[0]}-")
                ]
            return matches[0] if len(matches) == 1 else None

        for device in dr.async_entries_for_config_entry(device_registry, entry_id):
            serial_identifiers = {
                identifier
                for identifier in device.identifiers
                if identifier[0] == DOMAIN and identifier[1].startswith(serial_prefix)
            }
            device_ids = {
                identifier[1].removeprefix(device_id_prefix)
                for identifier in device.identifiers
                if identifier[0] == DOMAIN
                and identifier[1].startswith(device_id_prefix)
            }
            if not device_ids or (not serial_identifiers and len(device_ids) == 1):
                continue
            keep, *split_off = sorted(device_ids, key=owner_rank, reverse=True)
            via_device_id: str | UndefinedType | None = UNDEFINED
            if split_off:
                # the via device can be the bridge of a split off device; a node
                # resolves to the entry itself, as it has no via device
                bridge = device_registry.async_get_device_by_identifier(
                    (
                        DOMAIN,
                        f"{device_id_prefix}{keep.rpartition('-')[0]}-MatterNodeDevice",
                    ),
                    entry_id,
                )
                via_device_id = bridge.id if bridge and bridge.id != device.id else None
            device_registry.async_update_device(
                device.id,
                new_identifiers=device.identifiers
                - serial_identifiers
                - {(DOMAIN, f"{device_id_prefix}{d}") for d in split_off},
                via_device_id=via_device_id,
            )
            if not split_off:
                continue
            LOGGER.info(
                "Splitting %s off device %s, which is kept for %s",
                split_off,
                device.id,
                keep,
            )
            new_device_ids = {
                device_id: device_registry.async_get_or_create(
                    config_entry_id=entry_id,
                    identifiers={(DOMAIN, f"{device_id_prefix}{device_id}")},
                    name=device.name,
                    manufacturer=device.manufacturer,
                    model=device.model,
                    model_id=device.model_id,
                    serial_number=device.serial_number,
                    hw_version=device.hw_version,
                    sw_version=device.sw_version,
                    disabled_by=device.disabled_by,
                ).id
                for device_id in split_off
            }
            for entity in er.async_entries_for_device(
                entity_registry, device.id, include_disabled_entities=True
            ):
                if (
                    entity.platform == DOMAIN
                    and entity.config_entry_id == entry_id
                    and (owner := entity_owner(entity.unique_id, device_ids))
                    in new_device_ids
                ):
                    entity_registry.async_update_entity(
                        entity.entity_id, device_id=new_device_ids[owner]
                    )

    def _setup_node(self, node: MatterNode) -> None:
        """Set up an node."""
        LOGGER.debug("Setting up entities for node %s", node.node_id)
        try:
            # Process endpoints in order so the bridge device (endpoint 0) is
            # registered before any bridged child endpoint resolves it as its
            # via_device.
            for endpoint_id in sorted(node.endpoints):
                # Node endpoints are translated into HA devices
                self._setup_endpoint(node.endpoints[endpoint_id])
        except Exception as err:  # noqa: BLE001
            # We don't want to crash the whole setup when a single node fails to setup
            # for whatever reason, so we catch all exceptions here.
            LOGGER.exception(
                "Error setting up node %s: %s",
                node.node_id,
                err,
            )

    def _create_device_registry(
        self,
        endpoint: MatterEndpoint,
    ) -> None:
        """Create a device registry entry for a MatterNode."""
        server_info = cast(ServerInfoMessage, self.matter_client.server_info)

        # All endpoints of a composed device share a single device registry entry,
        # so derive that entry from the compose parent for every one of them.
        endpoint = get_device_endpoint(endpoint)

        # a Bridged Node without BridgedDeviceBasicInformation gets a device without its info
        basic_info = endpoint.device_info or clusters.BridgedDeviceBasicInformation()
        node_info = endpoint.node.device_info
        # use (first) DeviceType of the endpoint as fallback product name
        device_type = next(
            (
                x
                for x in endpoint.device_types
                if x.device_type != BridgedNode.device_type
            ),
            None,
        )
        name = (
            get_clean_name(basic_info.nodeLabel)
            or get_clean_name(basic_info.productLabel)
            or get_clean_name(basic_info.productName)
            or (device_type.__name__ if device_type else None)
        )

        device_registry = dr.async_get(self.hass)

        # handle bridged devices
        via_device_id: str | UndefinedType = UNDEFINED
        if endpoint.is_bridged_device and endpoint.node.endpoints[0] != endpoint:
            bridge_device_id = get_device_id(
                server_info,
                endpoint.node.endpoints[0],
            )
            via_device_id = dr.async_get_device_id_by_identifier(
                self.hass,
                (DOMAIN, f"{ID_TYPE_DEVICE_ID}_{bridge_device_id}"),
                config_entry_id=self.config_entry.entry_id,
            )

        node_device_id = get_device_id(
            server_info,
            endpoint,
        )
        serial_number: str | None = None
        # not an identifier: re-commissioned nodes and distinct devices can share it
        if (
            (basic_info_serial_number := basic_info.serialNumber)
            and "test" not in basic_info_serial_number.lower()
            # some bridges report their own serial number for the devices they bridge
            and not (
                isinstance(basic_info, clusters.BridgedDeviceBasicInformation)
                and node_info is not None
                and basic_info_serial_number == node_info.serialNumber
            )
        ):
            serial_number = basic_info_serial_number

        # Model name is the human readable name of the model/product name
        model_name = (
            # productLabel is optional but preferred (e.g. Hue Bloom)
            get_clean_name(basic_info.productLabel)
            # alternative is the productName (e.g. LCT001)
            or get_clean_name(basic_info.productName)
            # if no product name, use the device type name
            or (device_type.__name__ if device_type else None)
        )
        # Model ID is the non-human readable product ID
        # we prefer the matter product ID so we can look it up in Matter DCL
        if isinstance(basic_info, clusters.BridgedDeviceBasicInformation):
            # On bridged devices, the productID is not available
            model_id = None
        else:
            model_id = str(product_id) if (product_id := basic_info.productID) else None

        device_registry.async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            name=name,
            identifiers={(DOMAIN, f"{ID_TYPE_DEVICE_ID}_{node_device_id}")},
            hw_version=basic_info.hardwareVersionString,
            sw_version=basic_info.softwareVersionString,
            manufacturer=basic_info.vendorName
            or (node_info.vendorName if node_info is not None else None),
            model=model_name,
            model_id=model_id,
            serial_number=serial_number,
            via_device_id=via_device_id,
        )

    def _setup_endpoint(self, endpoint: MatterEndpoint) -> None:
        """Set up a MatterEndpoint as HA Device."""
        # pre-create device registry entry
        self._create_device_registry(endpoint)
        server_info = cast(ServerInfoMessage, self.matter_client.server_info)
        device_id = get_device_id(server_info, endpoint)
        legacy_device_id = get_legacy_device_id(server_info, endpoint)
        # run platform discovery from device type instances
        for entity_info in async_discover_entities(
            endpoint, server_info.schema_version
        ):
            discovery_key = get_discovery_key(endpoint, entity_info)
            if discovery_key in self.discovered_entities:
                continue
            if legacy_device_id != device_id:
                self._migrate_legacy_unique_id(
                    endpoint, entity_info, legacy_device_id, device_id
                )
            LOGGER.debug(
                "Creating %s entity for %s",
                entity_info.platform,
                entity_info.primary_attribute,
            )
            self.discovered_entities.add(discovery_key)
            new_entity = entity_info.entity_class(
                self.matter_client, endpoint, entity_info
            )
            self.platform_handlers[entity_info.platform]([new_entity])

    def _migrate_legacy_unique_id(
        self,
        endpoint: MatterEndpoint,
        entity_info: MatterEntityInfo,
        legacy_device_id: str,
        device_id: str,
    ) -> None:
        """Rename an entity registry entry still using the pre-1.5.0 unique_id.

        matter-python-client 1.5.0 changed `get_compose_parent()` to resolve to the
        top of a composition instead of the closest parent, which changes the
        unique_id of a part nested below another part. Rename the entity registry
        entry so its entity_id (and any automation referencing it) survives the
        client upgrade, instead of orphaning it and creating a new entity.
        """
        legacy_unique_id = get_entity_unique_id(legacy_device_id, endpoint, entity_info)
        entity_registry = er.async_get(self.hass)
        if entity_id := entity_registry.async_get_entity_id(
            entity_info.platform, DOMAIN, legacy_unique_id
        ):
            entity_registry.async_update_entity(
                entity_id,
                new_unique_id=get_entity_unique_id(device_id, endpoint, entity_info),
            )
