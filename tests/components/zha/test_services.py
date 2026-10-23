"""Tests for the ZHA services."""

from collections.abc import Callable, Coroutine
from unittest.mock import AsyncMock, patch

import pytest
from zha.zigbee.device import Device
from zigpy.const import SIG_EP_INPUT, SIG_EP_OUTPUT, SIG_EP_PROFILE, SIG_EP_TYPE
import zigpy.profiles.zha
from zigpy.typing import (
    UNDEFINED as ZIGPY_UNDEFINED,
    UndefinedType as ZigpyUndefinedType,
)
from zigpy.zcl.clusters import general

from homeassistant.components.zha.const import DOMAIN
from homeassistant.components.zha.helpers import get_zha_gateway
from homeassistant.components.zha.services import (
    SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
    SERVICE_ISSUE_ZIGBEE_GROUP_COMMAND,
    SERVICE_PERMIT,
    SERVICE_REMOVE,
    SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
    SERVICE_WARNING_DEVICE_SQUAWK,
    SERVICE_WARNING_DEVICE_WARN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

IEEE_SWITCH_DEVICE = "01:2d:6f:00:0a:90:69:e7"


@pytest.fixture
async def zha_switch_device(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., Device],
) -> None:
    """Set up ZHA with a single switch device."""
    await setup_zha()
    gateway = get_zha_gateway(hass)

    zigpy_device = zigpy_device_mock(
        {
            1: {
                SIG_EP_INPUT: [general.OnOff.cluster_id, general.Basic.cluster_id],
                SIG_EP_OUTPUT: [],
                SIG_EP_TYPE: zigpy.profiles.zha.DeviceType.ON_OFF_SWITCH,
                SIG_EP_PROFILE: zigpy.profiles.zha.PROFILE_ID,
            }
        },
        ieee=IEEE_SWITCH_DEVICE,
    )

    gateway.get_or_create_device(zigpy_device)
    await gateway.async_device_initialized(zigpy_device)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_services_registered_without_gateway(hass: HomeAssistant) -> None:
    """Test the actions are registered during component setup."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    for service in (
        SERVICE_PERMIT,
        SERVICE_REMOVE,
        SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
        SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
        SERVICE_ISSUE_ZIGBEE_GROUP_COMMAND,
        SERVICE_WARNING_DEVICE_SQUAWK,
        SERVICE_WARNING_DEVICE_WARN,
    ):
        assert hass.services.has_service(DOMAIN, service)


@pytest.mark.parametrize(
    ("service", "data"),
    [
        pytest.param(SERVICE_PERMIT, {}, id="permit"),
        pytest.param(SERVICE_REMOVE, {"ieee": IEEE_SWITCH_DEVICE}, id="remove"),
        pytest.param(
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                "ieee": IEEE_SWITCH_DEVICE,
                "endpoint_id": 1,
                "cluster_id": 6,
                "attribute": 0,
                "value": 1,
            },
            id="set_zigbee_cluster_attribute",
        ),
        pytest.param(
            SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
            {
                "ieee": IEEE_SWITCH_DEVICE,
                "endpoint_id": 1,
                "cluster_id": 6,
                "command": 0,
                "command_type": "server",
                "params": {},
            },
            id="issue_zigbee_cluster_command",
        ),
        pytest.param(
            SERVICE_ISSUE_ZIGBEE_GROUP_COMMAND,
            {"group": 1, "cluster_id": 6, "command": 0},
            id="issue_zigbee_group_command",
        ),
        pytest.param(
            SERVICE_WARNING_DEVICE_SQUAWK,
            {"ieee": IEEE_SWITCH_DEVICE},
            id="warning_device_squawk",
        ),
        pytest.param(
            SERVICE_WARNING_DEVICE_WARN,
            {"ieee": IEEE_SWITCH_DEVICE},
            id="warning_device_warn",
        ),
    ],
)
async def test_service_without_gateway_raises(
    hass: HomeAssistant, service: str, data: dict
) -> None:
    """Test the actions raise a user-facing error when no gateway is loaded."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, service, data, blocking=True)

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "no_gateway"


@pytest.mark.parametrize(
    ("service_manufacturer", "expected_manufacturer"),
    [
        pytest.param({}, ZIGPY_UNDEFINED, id="omitted"),
        pytest.param({"manufacturer": -1}, ZIGPY_UNDEFINED, id="minus_one"),
        pytest.param({"manufacturer": 0x1166}, 0x1166, id="explicit_code"),
    ],
)
@pytest.mark.usefixtures("zha_switch_device")
async def test_set_zigbee_cluster_attribute_manufacturer(
    hass: HomeAssistant,
    service_manufacturer: dict[str, int],
    expected_manufacturer: int | ZigpyUndefinedType,
) -> None:
    """Test the legacy -1 manufacturer sentinel no longer reaches zigpy."""
    with patch.object(
        Device, "write_zigbee_attribute", AsyncMock()
    ) as write_attribute_mock:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                "ieee": IEEE_SWITCH_DEVICE,
                "endpoint_id": 1,
                "cluster_id": general.OnOff.cluster_id,
                "attribute": general.OnOff.AttributeDefs.on_off.name,
                "value": 1,
            }
            | service_manufacturer,
            blocking=True,
        )

    assert (
        write_attribute_mock.await_args.kwargs["manufacturer"] == expected_manufacturer
    )
