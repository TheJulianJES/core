"""Test ZHA text entities."""

from collections.abc import Callable, Coroutine, Generator
from unittest.mock import patch

import pytest
from zha.application.platforms import ENTITY_REGISTRY, ClusterMatch
from zha.application.platforms.text import TextConfigurationEntity
from zha.application.platforms.text.const import TextMode as ZHATextMode
from zigpy.const import SIG_EP_INPUT, SIG_EP_OUTPUT, SIG_EP_PROFILE, SIG_EP_TYPE
from zigpy.device import ZigbeeDevice
from zigpy.profiles import zha
from zigpy.zcl import foundation as zcl_f
from zigpy.zcl.clusters import general

from homeassistant.components.text import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_MODE,
    ATTR_PATTERN,
    ATTR_VALUE,
    DOMAIN as TEXT_DOMAIN,
    SERVICE_SET_VALUE,
    TextMode,
)
from homeassistant.components.zha.helpers import (
    ZHADeviceProxy,
    ZHAGatewayProxy,
    get_zha_gateway,
    get_zha_gateway_proxy,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN, EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .common import find_entity_id, send_attributes_report, update_attribute_cache


class FakeLocationText(TextConfigurationEntity):
    """Text entity for the location description of the Basic cluster."""

    _unique_id_suffix = "location"
    _attribute_name = "location_desc"
    _attr_fallback_name = "Location"
    _attr_native_max = 16
    _attr_pattern = r"[A-Za-z ]*$"
    _cluster_match = ClusterMatch(
        server_clusters=frozenset({general.Basic.cluster_id}),
    )


class FakeUnlimitedText(FakeLocationText):
    """Text entity for a string attribute without a length limit of its own."""

    _attribute_name = "product_label"
    _attr_native_max = 255
    _attr_pattern = None


class FakePasswordText(FakeLocationText):
    """Text entity in password mode."""

    _attr_mode = ZHATextMode.PASSWORD


@pytest.fixture(autouse=True)
def text_platform_only() -> Generator[None]:
    """Only set up the text and required base platforms to speed up tests."""
    with patch(
        "homeassistant.components.zha.PLATFORMS",
        (Platform.SENSOR, Platform.TEXT),
    ):
        yield


@pytest.fixture
def text_class() -> type[FakeLocationText]:
    """Return the fake text entity class to discover."""
    return FakeLocationText


@pytest.fixture(autouse=True)
def register_fake_text(text_class: type[FakeLocationText]) -> Generator[None]:
    """Make zha discover the fake text entity on the Basic server cluster."""
    with patch.dict(
        ENTITY_REGISTRY,
        {
            general.Basic.cluster_id: [
                *ENTITY_REGISTRY[general.Basic.cluster_id],
                text_class,
            ]
        },
    ):
        yield


async def _setup_device(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
) -> tuple[str, general.Basic]:
    """Join a device with a location description, return the text entity ID."""
    await setup_zha()

    gateway = get_zha_gateway(hass)
    gateway_proxy: ZHAGatewayProxy = get_zha_gateway_proxy(hass)

    zigpy_device = zigpy_device_mock(
        {
            1: {
                SIG_EP_PROFILE: zha.PROFILE_ID,
                SIG_EP_TYPE: zha.DeviceType.ON_OFF_OUTPUT,
                SIG_EP_INPUT: [general.Basic.cluster_id],
                SIG_EP_OUTPUT: [],
            }
        },
    )
    cluster = zigpy_device.endpoints[1].basic
    cluster.PLUGGED_ATTR_READS = {
        "location_desc": "Kitchen",
        "product_label": "Kitchen",
    }
    update_attribute_cache(cluster)

    gateway.get_or_create_device(zigpy_device)
    await gateway.async_device_initialized(zigpy_device)
    await hass.async_block_till_done(wait_background_tasks=True)

    zha_device_proxy: ZHADeviceProxy = gateway_proxy.get_device_proxy(zigpy_device.ieee)
    entity_id = find_entity_id(Platform.TEXT, zha_device_proxy, hass)
    assert entity_id is not None

    return entity_id, cluster


async def test_text_entity(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
) -> None:
    """Test a ZHA text entity shows, updates and writes its attribute."""
    entity_id, cluster = await _setup_device(hass, setup_zha, zigpy_device_mock)

    state = hass.states.get(entity_id)
    assert state
    assert state.state == "Kitchen"
    assert state.attributes[ATTR_MIN] == 0
    assert state.attributes[ATTR_MAX] == 16
    assert state.attributes[ATTR_MODE] == TextMode.TEXT
    assert state.attributes[ATTR_PATTERN] == r"[A-Za-z ]*$"

    entry = entity_registry.async_get(entity_id)
    assert entry
    assert entry.entity_category == EntityCategory.CONFIG

    # A report from the device
    await send_attributes_report(
        hass, cluster, {general.Basic.AttributeDefs.location_desc.id: "Hallway"}
    )
    assert hass.states.get(entity_id).state == "Hallway"

    # A value the entity can't hold is unknown
    await send_attributes_report(
        hass, cluster, {general.Basic.AttributeDefs.location_desc.id: "Room 2"}
    )
    assert hass.states.get(entity_id).state == STATE_UNKNOWN

    # Changing the value writes the attribute
    await hass.services.async_call(
        TEXT_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: "Office"},
        blocking=True,
    )
    assert cluster.write_attributes.mock_calls[-1].args[0] == {
        "location_desc": "Office"
    }
    assert hass.states.get(entity_id).state == "Office"


@pytest.mark.parametrize(
    ("text_class", "mode", "value_in_error"),
    [
        (FakeLocationText, TextMode.TEXT, True),
        (FakePasswordText, TextMode.PASSWORD, False),
    ],
)
async def test_text_set_value_failure(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
    mode: TextMode,
    value_in_error: bool,
) -> None:
    """Test a failed write raises, without the value for a password."""
    entity_id, cluster = await _setup_device(hass, setup_zha, zigpy_device_mock)
    assert hass.states.get(entity_id).attributes[ATTR_MODE] == mode
    cluster._write_attributes.return_value = [
        zcl_f.WriteAttributesResponse(
            [
                zcl_f.WriteAttributesStatusRecord(
                    status=zcl_f.Status.READ_ONLY,
                    attrid=general.Basic.AttributeDefs.location_desc.id,
                )
            ]
        )
    ]

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            TEXT_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: "Office"},
            blocking=True,
        )

    assert ("Office" in str(exc_info.value)) is value_in_error
    assert hass.states.get(entity_id).state == "Kitchen"


@pytest.mark.parametrize("text_class", [FakeUnlimitedText])
async def test_text_set_value_too_long(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
) -> None:
    """Test a value within HA's limit but too long for the attribute raises."""
    entity_id, cluster = await _setup_device(hass, setup_zha, zigpy_device_mock)
    assert hass.states.get(entity_id).attributes[ATTR_MAX] == 255

    # 200 characters, but 400 bytes in UTF-8
    with pytest.raises(
        HomeAssistantError, match="Failed to write attribute product_label"
    ):
        await hass.services.async_call(
            TEXT_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: "ä" * 200},
            blocking=True,
        )

    # Refused before anything was sent
    assert cluster.write_attributes.call_count == 1
    cluster._write_attributes.assert_not_called()
    assert hass.states.get(entity_id).state == "Kitchen"
