"""Matter event entities from Node events."""

from dataclasses import dataclass
from typing import Any, override

from chip.clusters import Objects as clusters
from matter_server.client.models import device_types
from matter_server.client.models.node import MatterEndpoint
from matter_server.common.models import EventType, MatterNodeEvent

from homeassistant.components.event import (
    ATTR_PRESS_COUNT,
    ButtonEventType,
    EventDeviceClass,
    EventEntity,
    EventEntityDescription,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import LOGGER
from .entity import MatterEntity, MatterEntityDescription
from .helpers import MatterConfigEntry, get_device_endpoint
from .models import MatterDiscoverySchema, SemanticTag

SwitchFeature = clusters.Switch.Bitmaps.Feature

EVENT_TYPES_MAP = {
    # mapping from raw event IDs to translation keys
    0: "switch_latched",  # clusters.Switch.Events.SwitchLatched
    1: "initial_press",  # clusters.Switch.Events.InitialPress
    2: "long_press",  # clusters.Switch.Events.LongPress
    3: "short_release",  # clusters.Switch.Events.ShortRelease
    4: "long_release",  # clusters.Switch.Events.LongRelease
    5: "multi_press_ongoing",  # clusters.Switch.Events.MultiPressOngoing
    6: "multi_press_complete",  # clusters.Switch.Events.MultiPressComplete
}

# IKEA uses a Switches.Custom semantic tag labeled "rotary" for the Generic
# Switch endpoints that report the rotation of a scroll wheel as multi-press
# counts, one endpoint per direction (tagged Switches.Up and Switches.Down)
# and one pair per layer (tagged with the same Common Position.Column label).
# These are no buttons, so they get a rotary event entity per layer
# instead of the standard button event entity.
ROTARY_SWITCH_TAG = SemanticTag(
    namespace_id=0x43,  # Switches namespace
    tag=0x08,  # Custom
    label="rotary",
)
SWITCH_UP_TAG = SemanticTag(namespace_id=0x43, tag=0x03)  # Switches.Up
SWITCH_DOWN_TAG = SemanticTag(namespace_id=0x43, tag=0x04)  # Switches.Down
COLUMN_TAG = SemanticTag(namespace_id=0x08, tag=0x06)  # Common Position.Column

# event type and event data of the rotary event entity, as proposed in
# https://github.com/zigpy/backlog/issues/84
EVENT_TYPE_ROTATION = "rotation"
ATTR_DIRECTION = "direction"
ATTR_STEPS = "steps"
DIRECTION_CLOCKWISE = "clockwise"
DIRECTION_COUNTER_CLOCKWISE = "counter_clockwise"

# Matter-specific event types of the standard button event entity
EVENT_TYPE_INITIAL_PRESS = "initial_press"
EVENT_TYPE_MULTI_PRESS_ONGOING = "multi_press_ongoing"
EVENT_TYPE_SWITCH_LATCHED = "switch_latched"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MatterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Matter switches from Config Entry."""
    matter = config_entry.runtime_data.adapter
    matter.register_platform_handler(Platform.EVENT, async_add_entities)


@dataclass(frozen=True, kw_only=True)
class MatterEventEntityDescription(EventEntityDescription, MatterEntityDescription):
    """Describe Matter Event entities."""


class MatterEventEntityBase(MatterEntity, EventEntity):
    """Base class for Matter Event entities."""

    @override
    async def async_added_to_hass(self) -> None:
        """Handle being added to Home Assistant."""
        await super().async_added_to_hass()

        # subscribe to NodeEvent events
        self.async_on_remove(
            self.matter_client.subscribe_events(
                callback=self._on_matter_node_event,
                event_filter=EventType.NODE_EVENT,
                node_filter=self._endpoint.node.node_id,
            )
        )

    @callback
    @override
    def _update_from_device(self) -> None:
        """Call when Node attribute(s) changed."""

    @callback
    def _on_matter_node_event(
        self,
        event: EventType,
        data: MatterNodeEvent,
    ) -> None:
        """Call on NodeEvent."""
        if (
            data.endpoint_id not in self._switch_endpoint_ids
            or data.cluster_id != clusters.Switch.id
        ):
            return
        self._handle_switch_event(data)

    @property
    def _switch_endpoint_ids(self) -> set[int]:
        """Return the endpoints whose Switch events this entity handles."""
        return {self._endpoint.endpoint_id}

    @callback
    def _handle_switch_event(self, data: MatterNodeEvent) -> None:
        """Handle a Switch cluster event."""
        raise NotImplementedError


class MatterButtonEventEntity(MatterEventEntityBase):
    """Matter Event entity using the standard button event types."""

    @override
    @callback
    def _update_from_device(self) -> None:
        """Call when Node attribute(s) changed."""
        # (re)fill the event types based on the features the switch supports,
        # as the feature map can change during the lifetime of the entity
        event_types: list[str] = []
        feature_map = int(
            self.get_matter_attribute_value(clusters.Switch.Attributes.FeatureMap)
        )
        if feature_map & SwitchFeature.kLatchingSwitch:
            # a latching switch only supports the switch_latched event
            event_types.append(EVENT_TYPE_SWITCH_LATCHED)
        elif feature_map & SwitchFeature.kMomentarySwitch:
            action_switch = bool(feature_map & SwitchFeature.kActionSwitch)
            if not action_switch:
                # InitialPress is sent when the button goes down
                event_types.append(ButtonEventType.PRESS_START)
            if feature_map & SwitchFeature.kMomentarySwitchRelease:
                event_types.append(ButtonEventType.PRESS_END)
            if feature_map & SwitchFeature.kMomentarySwitchLongPress:
                event_types.append(ButtonEventType.LONG_PRESS_START)
                event_types.append(ButtonEventType.LONG_PRESS_END)
            if feature_map & SwitchFeature.kMomentarySwitchMultiPress:
                event_types.append(ButtonEventType.SINGLE_PRESS)
                event_types.append(ButtonEventType.MULTI_PRESS)
            # Matter-specific event types
            if action_switch:
                # an action switch sends InitialPress only once per
                # multi-press sequence, so it is not a press_start
                event_types.append(EVENT_TYPE_INITIAL_PRESS)
            elif feature_map & SwitchFeature.kMomentarySwitchMultiPress:
                # an action switch reports only the final result
                # of a multi-press sequence
                event_types.append(EVENT_TYPE_MULTI_PRESS_ONGOING)

        self._attr_event_types = event_types

    @override
    @callback
    def _handle_switch_event(self, data: MatterNodeEvent) -> None:
        """Handle a Switch cluster event."""
        # pass the event data as-is (such as the advanced Position data)
        event_data = dict(data.data or {})
        event_type: str | None
        match data.event_id:
            case clusters.Switch.Events.SwitchLatched.event_id:
                event_type = EVENT_TYPE_SWITCH_LATCHED
            case clusters.Switch.Events.InitialPress.event_id:
                event_type = ButtonEventType.PRESS_START
                if ButtonEventType.PRESS_START not in self.event_types:
                    event_type = EVENT_TYPE_INITIAL_PRESS
            case clusters.Switch.Events.ShortRelease.event_id:
                event_type = ButtonEventType.PRESS_END
            case clusters.Switch.Events.LongPress.event_id:
                event_type = ButtonEventType.LONG_PRESS_START
            case clusters.Switch.Events.LongRelease.event_id:
                event_type = ButtonEventType.LONG_PRESS_END
            case clusters.Switch.Events.MultiPressOngoing.event_id:
                event_type = EVENT_TYPE_MULTI_PRESS_ONGOING
                event_data[ATTR_PRESS_COUNT] = event_data.get(
                    "currentNumberOfPressesCounted", 2
                )
            case clusters.Switch.Events.MultiPressComplete.event_id:
                presses = event_data.get("totalNumberOfPressesCounted")
                if not isinstance(presses, int) or presses < 1:
                    # a count of 0 means the multi-press sequence was aborted,
                    # e.g. because the number of presses exceeded MultiPressMax;
                    # an event without a count can't be classified
                    return
                if presses == 1:
                    event_type = ButtonEventType.SINGLE_PRESS
                else:
                    event_type = ButtonEventType.MULTI_PRESS
                    event_data[ATTR_PRESS_COUNT] = presses
            case _:
                event_type = None

        if event_type is None or event_type not in self.event_types:
            # this should not happen, but guard for bad things
            # some remotes send events that they do not report as supported (sigh...)
            return

        self._trigger_event(event_type, event_data)
        self.async_write_ha_state()


def _get_bridged_device_id(endpoint: MatterEndpoint) -> int | None:
    """Return the endpoint ID of the bridged device an endpoint belongs to.

    Returns None for the endpoints of a node that isn't bridged,
    as they all belong to the node's device.
    """
    device_endpoint = get_device_endpoint(endpoint)
    if device_endpoint.is_bridged_device:
        return device_endpoint.endpoint_id
    return None


class MatterRotaryEventEntity(MatterEventEntityBase):
    """Matter Event entity for a rotary control (such as a scroll wheel).

    The rotation is reported as multi-press counts on two Generic Switch
    endpoints, one per direction. This entity is created for the clockwise
    (Switches.Up) endpoint and also handles the matching counter-clockwise
    (Switches.Down) endpoint of the same layer.
    """

    _attr_event_types = [EVENT_TYPE_ROTATION]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the entity."""
        super().__init__(*args, **kwargs)
        self._directions: dict[int, str] = {
            self._endpoint.endpoint_id: DIRECTION_CLOCKWISE
        }
        # pair with the counter-clockwise endpoint of the same layer on the
        # same device, only if that endpoint can be told apart unambiguously
        device = _get_bridged_device_id(self._endpoint)
        layer = COLUMN_TAG.get_label(self._endpoint)
        same_layer = [
            endpoint
            for endpoint in self._endpoint.node.endpoints.values()
            if _get_bridged_device_id(endpoint) == device
            and ROTARY_SWITCH_TAG.on_endpoint(endpoint)
            and COLUMN_TAG.get_label(endpoint) == layer
        ]
        up = [ep for ep in same_layer if SWITCH_UP_TAG.on_endpoint(ep)]
        down = [ep for ep in same_layer if SWITCH_DOWN_TAG.on_endpoint(ep)]
        if up == [self._endpoint] and len(down) == 1:
            self._directions[down[0].endpoint_id] = DIRECTION_COUNTER_CLOCKWISE
        else:
            LOGGER.debug(
                "No unambiguous counter-clockwise endpoint for rotary endpoint %s",
                self._endpoint.endpoint_id,
            )
        # the count of presses (detents) of the current multi-press sequence
        # already reported, per endpoint
        self._reported_counts: dict[int, int] = {}
        self._last_event_number: int | None = None

    @property
    @override
    def _switch_endpoint_ids(self) -> set[int]:
        """Return the endpoints whose Switch events this entity handles."""
        return set(self._directions)

    @override
    @callback
    def _handle_switch_event(self, data: MatterNodeEvent) -> None:
        """Handle a Switch cluster event."""
        # events can be delivered again after a resubscription,
        # and replaying them would report rotation that didn't happen;
        # event numbers increase for the life of a node, also across restarts
        if (
            self._last_event_number is not None
            and data.event_number <= self._last_event_number
        ):
            return
        self._last_event_number = data.event_number

        endpoint_id = data.endpoint_id
        event_data = data.data or {}
        match data.event_id:
            case clusters.Switch.Events.MultiPressOngoing.event_id:
                count = event_data.get("currentNumberOfPressesCounted")
                sequence_complete = False
            case clusters.Switch.Events.MultiPressComplete.event_id:
                count = event_data.get("totalNumberOfPressesCounted")
                sequence_complete = True
            case _:
                # InitialPress and ShortRelease carry no rotation amount
                return
        reported = self._reported_counts.get(endpoint_id, 0)
        if isinstance(count, int) and (
            count < reported or (not sequence_complete and count == reported)
        ):
            # the count of a sequence only increases with each MultiPressOngoing,
            # so a lower (or, for MultiPressOngoing, equal) count means
            # a new sequence started and the previous completion was missed
            reported = 0
        if sequence_complete:
            self._reported_counts.pop(endpoint_id, None)
        elif isinstance(count, int):
            self._reported_counts[endpoint_id] = count
        if not isinstance(count, int) or (steps := count - reported) <= 0:
            return

        self._trigger_event(
            EVENT_TYPE_ROTATION,
            {ATTR_DIRECTION: self._directions[endpoint_id], ATTR_STEPS: steps},
        )
        self.async_write_ha_state()


class MatterEventEntity(MatterEventEntityBase):
    """Legacy Matter Event entity.

    Replaced by MatterButtonEventEntity, and by MatterRotaryEventEntity on
    rotary endpoints. Deprecated and disabled by default in HA 2027.1,
    remove in 2027.7.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the entity."""
        super().__init__(*args, **kwargs)
        # fill the event types based on the features the switch supports
        event_types: list[str] = []
        feature_map = int(
            self.get_matter_attribute_value(clusters.Switch.Attributes.FeatureMap)
        )
        if feature_map & SwitchFeature.kLatchingSwitch:
            # a latching switch only supports switch_latched event
            event_types.append("switch_latched")
        elif feature_map & SwitchFeature.kMomentarySwitchMultiPress:
            # Momentary switch with multi press support
            # NOTE: We ignore 'multi press ongoing' as it doesn't make a lot
            # of sense and many devices do not support it.
            # Instead we report on the 'multi press complete' event with the number
            # of presses.
            max_presses_supported = self.get_matter_attribute_value(
                clusters.Switch.Attributes.MultiPressMax
            )
            max_presses_supported = min(max_presses_supported or 2, 8)
            for i in range(max_presses_supported):
                event_types.append(f"multi_press_{i + 1}")  # noqa: PERF401
        elif feature_map & SwitchFeature.kMomentarySwitch:
            # momentary switch without multi press support
            event_types.append("initial_press")
            if feature_map & SwitchFeature.kMomentarySwitchRelease:
                # momentary switch without multi press support
                # can optionally support release
                event_types.append("short_release")

        # a momentary switch can optionally support long press
        if feature_map & SwitchFeature.kMomentarySwitchLongPress:
            event_types.append("long_press")
            event_types.append("long_release")

        self._attr_event_types = event_types

    @override
    @callback
    def _handle_switch_event(self, data: MatterNodeEvent) -> None:
        """Handle a Switch cluster event."""
        event_type: str | None = EVENT_TYPES_MAP.get(data.event_id)
        if data.event_id == clusters.Switch.Events.MultiPressComplete.event_id:
            # multi press event
            presses = (data.data or {}).get("totalNumberOfPressesCounted", 1)
            event_type = f"multi_press_{presses}"

        if event_type is None:
            LOGGER.debug(
                "Ignoring unknown switch event id %s for %s",
                data.event_id,
                self.entity_id,
            )
            return

        if event_type not in self.event_types:
            # this should not happen, but guard for bad things
            # some remotes send events that they do not report as supported (sigh...)
            return

        # pass the rest of the data as-is (such as the advanced Position data)
        self._trigger_event(event_type, data.data)
        self.async_write_ha_state()


# Discovery schema(s) to map Matter Attributes to HA entities
DISCOVERY_SCHEMAS = [
    MatterDiscoverySchema(
        platform=Platform.EVENT,
        entity_description=MatterEventEntityDescription(
            key="GenericSwitchButton",
            device_class=EventDeviceClass.BUTTON,
            translation_key="button",
        ),
        entity_class=MatterButtonEventEntity,
        required_attributes=(
            clusters.Switch.Attributes.CurrentPosition,
            clusters.Switch.Attributes.FeatureMap,
        ),
        device_type=(device_types.GenericSwitch,),
        absent_semantic_tags=(ROTARY_SWITCH_TAG,),
        optional_attributes=(
            clusters.Switch.Attributes.NumberOfPositions,
            clusters.FixedLabel.Attributes.LabelList,
        ),
        allow_multi=True,  # also used for the legacy event and sensor entity
    ),
    MatterDiscoverySchema(
        platform=Platform.EVENT,
        entity_description=MatterEventEntityDescription(
            key="GenericSwitchRotary",
            translation_key="rotary",
        ),
        entity_class=MatterRotaryEventEntity,
        required_attributes=(
            clusters.Switch.Attributes.CurrentPosition,
            clusters.Switch.Attributes.FeatureMap,
        ),
        device_type=(device_types.GenericSwitch,),
        semantic_tags=(ROTARY_SWITCH_TAG, SWITCH_UP_TAG),
        featuremap_contains=SwitchFeature.kMomentarySwitchMultiPress,
        allow_multi=True,  # also used for the legacy event and sensor entity
    ),
    MatterDiscoverySchema(
        platform=Platform.EVENT,
        entity_description=MatterEventEntityDescription(
            key="GenericSwitch",
            device_class=EventDeviceClass.BUTTON,
            translation_key="button_deprecated",
            # Deprecated and disabled by default in HA 2027.1, remove in 2027.7
            entity_registry_enabled_default=False,
        ),
        entity_class=MatterEventEntity,
        required_attributes=(
            clusters.Switch.Attributes.CurrentPosition,
            clusters.Switch.Attributes.FeatureMap,
        ),
        device_type=(device_types.GenericSwitch,),
        optional_attributes=(
            clusters.Switch.Attributes.NumberOfPositions,
            clusters.FixedLabel.Attributes.LabelList,
        ),
        allow_multi=True,  # also used for sensor (current position) entity
    ),
]
