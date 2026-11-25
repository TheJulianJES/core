"""Test ZHA entities."""

from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from pytest_homeassistant.common import MockConfigEntry
from zha.quirks import DEVICE_REGISTRY
from zhaquirks.builder import QuirkBuilder
from zigpy.application import ControllerApplication
from zigpy.device import ZigbeeDevice
from zigpy.profiles import zha
from zigpy.zcl.clusters import general

from homeassistant.components.zha.const import DOMAIN
from homeassistant.components.zha.helpers import get_zha_gateway, get_zha_gateway_proxy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import (
    FIXTURE_GRP_WITH_ENTITIES_ID,
    FIXTURE_GRP_WITH_ENTITIES_NAME,
    SIG_EP_INPUT,
    SIG_EP_OUTPUT,
    SIG_EP_PROFILE,
    SIG_EP_TYPE,
)


async def test_device_registry_via_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test a ZHA device links to the coordinator device via via_device_id."""

    await setup_zha()
    gateway = get_zha_gateway(hass)

    zigpy_device = zigpy_device_mock(
        {
            1: {
                SIG_EP_INPUT: [general.Basic.cluster_id],
                SIG_EP_OUTPUT: [],
                SIG_EP_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                SIG_EP_PROFILE: zha.PROFILE_ID,
            }
        },
    )

    zha_device = gateway.get_or_create_device(zigpy_device)
    await gateway.async_device_initialized(zigpy_device)
    await hass.async_block_till_done(wait_background_tasks=True)

    coordinator_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, str(gateway.state.node_info.ieee)), config_entry.entry_id
    )
    assert coordinator_device is not None
    # The coordinator itself is not linked to a via device
    assert coordinator_device.via_device_id is None

    reg_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, str(zha_device.ieee)), config_entry.entry_id
    )
    assert reg_device is not None
    assert reg_device.via_device_id == coordinator_device.id


async def test_device_registry_via_device_without_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test a ZHA device without entities links to the coordinator device."""

    await setup_zha()
    gateway = get_zha_gateway(hass)

    zigpy_device = zigpy_device_mock(
        {
            1: {
                SIG_EP_INPUT: [],
                SIG_EP_OUTPUT: [],
                SIG_EP_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                SIG_EP_PROFILE: zha.PROFILE_ID,
            }
        },
        ieee="11:22:33:44:55:66:77:88",
    )

    zha_device = gateway.get_or_create_device(zigpy_device)
    await gateway.async_device_initialized(zigpy_device)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert not zha_device.platform_entities

    coordinator_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, str(gateway.state.node_info.ieee)), config_entry.entry_id
    )
    assert coordinator_device is not None

    reg_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, str(zha_device.ieee)), config_entry.entry_id
    )
    assert reg_device is not None
    assert reg_device.via_device_id == coordinator_device.id


@pytest.mark.parametrize(
    "in_clusters",
    [
        # The entities' device info updates the registry entry
        [general.Basic.cluster_id, general.OnOff.cluster_id],
        # Without entities, only the registry entry ZHA creates names the device
        [],
    ],
)
async def test_device_registry_uses_quirk_friendly_name(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
    device_registry: dr.DeviceRegistry,
    in_clusters: list[int],
) -> None:
    """Test a device shows a quirk's friendly name instead of the reported names."""
    await setup_zha()
    gateway = get_zha_gateway(hass)
    gateway_proxy = get_zha_gateway_proxy(hass)

    with DEVICE_REGISTRY.preserve_state():
        (
            QuirkBuilder("Fake Manufacturer", "Fake Model")
            .friendly_name(manufacturer="Friendly Manufacturer", model="Friendly Model")
            .add_to_registry()
        )
        zigpy_device = DEVICE_REGISTRY.resolve(
            zigpy_device_mock(
                {
                    1: {
                        SIG_EP_INPUT: in_clusters,
                        SIG_EP_OUTPUT: [],
                        SIG_EP_TYPE: zha.DeviceType.ON_OFF_SWITCH,
                        SIG_EP_PROFILE: zha.PROFILE_ID,
                    }
                },
                ieee="01:2d:6f:00:0a:90:69:e8",
                manufacturer="Fake Manufacturer",
                model="Fake Model",
            )
        )
        gateway.get_or_create_device(zigpy_device)
        await gateway.async_device_initialized(zigpy_device)
        await hass.async_block_till_done(wait_background_tasks=True)

    proxy = gateway_proxy.get_device_proxy(zigpy_device.ieee)
    assert proxy is not None
    assert proxy.device.quirk_applied
    assert bool(proxy.device.platform_entities) is bool(in_clusters)

    # The library reports the device's own names separately
    assert proxy.device.manufacturer == "Fake Manufacturer"
    assert proxy.device.model == "Fake Model"

    assert proxy.device_info["manufacturer"] == "Friendly Manufacturer"
    assert proxy.device_info["model"] == "Friendly Model"

    device = device_registry.async_get(proxy.device_id)
    assert device is not None
    assert device.manufacturer == "Friendly Manufacturer"
    assert device.model == "Friendly Model"
    assert device.name == "Friendly Manufacturer Friendly Model"


async def test_group_entity_name_and_device_info(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[Any, Any, None]],
    zigpy_app_controller_with_group: ControllerApplication,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that group entities use group device info and return None for name."""
    await setup_zha()

    gateway_proxy = get_zha_gateway_proxy(hass)
    group_proxy = gateway_proxy.group_proxies[FIXTURE_GRP_WITH_ENTITIES_ID]
    assert group_proxy.device_id is not None

    # Find the group entity
    entity_entries = er.async_entries_for_device(
        entity_registry, group_proxy.device_id, include_disabled_entities=True
    )
    assert len(entity_entries) > 0

    # The entity should use the group device name (friendly name = group name)
    entity = hass.states.get(entity_entries[0].entity_id)
    assert entity is not None
    assert entity.name == FIXTURE_GRP_WITH_ENTITIES_NAME

    # Verify device info points to the group device
    device = device_registry.async_get(group_proxy.device_id)
    assert device is not None
    config_entry_id = gateway_proxy.config_entry.entry_id
    expected_identifier = (
        f"{config_entry_id}_group_0x{FIXTURE_GRP_WITH_ENTITIES_ID:04x}"
    )
    assert group_proxy.device_identifier == expected_identifier
    assert ("zha", expected_identifier) in device.identifiers
    assert device.manufacturer == "Zigbee"
    assert device.model == "Group"
    assert device.entry_type == dr.DeviceEntryType.SERVICE
