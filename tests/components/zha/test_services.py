"""Tests for the ZHA services."""

from collections.abc import Callable, Coroutine
import re
from typing import Any
from unittest.mock import AsyncMock, _Call, call, patch

import pytest
from zha.application.platforms.siren import (
    SirenLevel,
    SquawkMode,
    Strobe,
    StrobeLevel,
    WarningMode,
)
from zha.zigbee.device import BaseDevice
from zigpy.application import ControllerApplication
from zigpy.device import GreenPowerDevice as ZigpyGreenPowerDevice, ZigbeeDevice
from zigpy.exceptions import DeliveryError
from zigpy.profiles import zha
from zigpy.typing import UNDEFINED as ZIGPY_UNDEFINED
from zigpy.zcl.clusters import general, security
from zigpy.zgp.types import ApplicationID, SrcID

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
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component

from .conftest import (
    FIXTURE_GRP_WITH_ENTITIES_ID,
    SIG_EP_INPUT,
    SIG_EP_OUTPUT,
    SIG_EP_PROFILE,
    SIG_EP_TYPE,
)

IEEE_SWITCH_DEVICE = "01:2d:6f:00:0a:90:69:e7"
SET_ATTRIBUTE_DATA = {"endpoint_id": 1, "cluster_id": 6, "attribute": 0, "value": 1}
CLUSTER_COMMAND_DATA = {
    "endpoint_id": 1,
    "cluster_id": 6,
    "command": 0,
    "command_type": "server",
    "params": {},
}
GROUP_COMMAND_DATA = {
    "group": FIXTURE_GRP_WITH_ENTITIES_ID,
    "cluster_id": 6,
    "command": 0,
}
SWITCH_ENDPOINTS = {
    1: {
        SIG_EP_INPUT: [general.Basic.cluster_id, general.OnOff.cluster_id],
        SIG_EP_OUTPUT: [],
        SIG_EP_TYPE: zha.DeviceType.ON_OFF_SWITCH,
        SIG_EP_PROFILE: zha.PROFILE_ID,
    }
}
WARNING_DEVICE_ENDPOINTS = {
    1: {
        SIG_EP_INPUT: [
            general.Basic.cluster_id,
            security.IasZone.cluster_id,
            security.IasWd.cluster_id,
        ],
        SIG_EP_OUTPUT: [],
        SIG_EP_TYPE: zha.DeviceType.IAS_WARNING_DEVICE,
        SIG_EP_PROFILE: zha.PROFILE_ID,
    }
}


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


async def _async_join_device(
    hass: HomeAssistant,
    zigpy_device_mock: Callable[..., ZigbeeDevice],
    endpoints: dict[int, dict[str, Any]],
) -> BaseDevice:
    """Join a device to the ZHA gateway."""
    gateway = get_zha_gateway(hass)
    zigpy_device = zigpy_device_mock(endpoints, ieee=IEEE_SWITCH_DEVICE)
    zha_device = gateway.get_or_create_device(zigpy_device)
    await gateway.async_device_initialized(zigpy_device)
    await hass.async_block_till_done(wait_background_tasks=True)

    return zha_device


@pytest.mark.parametrize(
    ("service", "data", "method", "expected_call"),
    [
        pytest.param(
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            SET_ATTRIBUTE_DATA,
            "write_zigbee_attribute",
            call(1, 6, 0, 1, cluster_type="in", manufacturer=ZIGPY_UNDEFINED),
            id="set_zigbee_cluster_attribute",
        ),
        pytest.param(
            SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
            {**CLUSTER_COMMAND_DATA, "manufacturer": 0x1234},
            "issue_cluster_command",
            call(1, 6, 0, "server", None, {}, cluster_type="in", manufacturer=0x1234),
            id="issue_zigbee_cluster_command",
        ),
    ],
)
async def test_cluster_service(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
    service: str,
    data: dict,
    method: str,
    expected_call: _Call,
) -> None:
    """Test the cluster actions are forwarded to the device."""
    await setup_zha()
    zha_device = await _async_join_device(hass, zigpy_device_mock, SWITCH_ENDPOINTS)

    with patch.object(zha_device, method, AsyncMock()) as method_mock:
        await hass.services.async_call(
            DOMAIN, service, {"ieee": IEEE_SWITCH_DEVICE, **data}, blocking=True
        )

    assert method_mock.await_args_list == [expected_call]


@pytest.mark.parametrize(
    ("service", "data", "method", "side_effect", "message"),
    [
        pytest.param(
            SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
            CLUSTER_COMMAND_DATA,
            "issue_cluster_command",
            DeliveryError(
                "Failed to deliver packet: <TXStatus.MAC_CHANNEL_ACCESS_FAILURE: 225>",
                225,
            ),
            (
                "Failed to send request: Failed to deliver packet: "
                "<TXStatus.MAC_CHANNEL_ACCESS_FAILURE: 225>"
            ),
            id="issue_cluster_command_delivery_error",
        ),
        pytest.param(
            SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
            CLUSTER_COMMAND_DATA,
            "issue_cluster_command",
            TimeoutError(),
            "Failed to send request: device did not respond",
            id="issue_cluster_command_timeout",
        ),
        pytest.param(
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            SET_ATTRIBUTE_DATA,
            "write_zigbee_attribute",
            DeliveryError("Failed to deliver packet", 225),
            "Failed to send request: Failed to deliver packet",
            id="set_zigbee_cluster_attribute_delivery_error",
        ),
    ],
)
async def test_cluster_service_radio_failure(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
    service: str,
    data: dict,
    method: str,
    side_effect: Exception,
    message: str,
) -> None:
    """Test a radio failure in the cluster actions raises a readable error."""
    await setup_zha()
    zha_device = await _async_join_device(hass, zigpy_device_mock, SWITCH_ENDPOINTS)

    with (
        patch.object(zha_device, method, side_effect=side_effect),
        pytest.raises(HomeAssistantError, match=re.escape(message)),
    ):
        await hass.services.async_call(
            DOMAIN, service, {"ieee": IEEE_SWITCH_DEVICE, **data}, blocking=True
        )


@pytest.mark.parametrize(
    ("service", "data"),
    [
        pytest.param(
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            SET_ATTRIBUTE_DATA,
            id="set_zigbee_cluster_attribute",
        ),
        pytest.param(
            SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
            CLUSTER_COMMAND_DATA,
            id="issue_zigbee_cluster_command",
        ),
        pytest.param(SERVICE_WARNING_DEVICE_SQUAWK, {}, id="warning_device_squawk"),
        pytest.param(SERVICE_WARNING_DEVICE_WARN, {}, id="warning_device_warn"),
    ],
)
async def test_service_unknown_device_raises(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    service: str,
    data: dict,
) -> None:
    """Test the device actions reject an unknown device."""
    await setup_zha()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, service, {"ieee": IEEE_SWITCH_DEVICE, **data}, blocking=True
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "device_not_found"
    assert err.value.translation_placeholders == {"ieee": IEEE_SWITCH_DEVICE}


@pytest.mark.parametrize(
    ("service", "method", "expected_call"),
    [
        pytest.param(
            SERVICE_WARNING_DEVICE_SQUAWK,
            "async_squawk",
            call(
                mode=SquawkMode.Armed,
                strobe=Strobe.Strobe,
                squawk_level=SirenLevel.High_level_sound,
            ),
            id="warning_device_squawk",
        ),
        pytest.param(
            SERVICE_WARNING_DEVICE_WARN,
            "async_turn_on",
            call(
                tone=WarningMode.Emergency,
                volume_level=SirenLevel.High_level_sound,
                duration=5,
                strobe=Strobe.Strobe,
                strobe_duty_cycle=0,
                strobe_intensity=StrobeLevel.High_level_strobe,
            ),
            id="warning_device_warn",
        ),
    ],
)
async def test_warning_device_service(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
    service: str,
    method: str,
    expected_call: _Call,
) -> None:
    """Test the warning device actions are forwarded to the siren."""
    await setup_zha()
    zha_device = await _async_join_device(
        hass, zigpy_device_mock, WARNING_DEVICE_ENDPOINTS
    )
    siren = zha_device.get_entity(Platform.SIREN)

    with patch.object(siren, method, AsyncMock()) as method_mock:
        await hass.services.async_call(
            DOMAIN, service, {"ieee": IEEE_SWITCH_DEVICE}, blocking=True
        )

    assert method_mock.await_args_list == [expected_call]


@pytest.mark.parametrize(
    ("service", "method"),
    [
        pytest.param(
            SERVICE_WARNING_DEVICE_SQUAWK, "async_squawk", id="warning_device_squawk"
        ),
        pytest.param(
            SERVICE_WARNING_DEVICE_WARN, "async_turn_on", id="warning_device_warn"
        ),
    ],
)
async def test_warning_device_service_radio_failure(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
    service: str,
    method: str,
) -> None:
    """Test a radio failure in the warning device actions raises a readable error."""
    await setup_zha()
    zha_device = await _async_join_device(
        hass, zigpy_device_mock, WARNING_DEVICE_ENDPOINTS
    )
    siren = zha_device.get_entity(Platform.SIREN)

    with (
        patch.object(
            siren, method, side_effect=DeliveryError("Failed to deliver packet", 225)
        ),
        pytest.raises(
            HomeAssistantError,
            match="Failed to send request: Failed to deliver packet",
        ),
    ):
        await hass.services.async_call(
            DOMAIN, service, {"ieee": IEEE_SWITCH_DEVICE}, blocking=True
        )


@pytest.mark.parametrize(
    "service", [SERVICE_WARNING_DEVICE_SQUAWK, SERVICE_WARNING_DEVICE_WARN]
)
async def test_warning_device_service_without_siren_raises(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., ZigbeeDevice],
    service: str,
) -> None:
    """Test the warning device actions reject a device without a siren."""
    await setup_zha()
    await _async_join_device(hass, zigpy_device_mock, SWITCH_ENDPOINTS)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, service, {"ieee": IEEE_SWITCH_DEVICE}, blocking=True
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "no_warning_device"
    assert err.value.translation_placeholders == {"ieee": IEEE_SWITCH_DEVICE}


@pytest.mark.usefixtures("zigpy_app_controller_with_group")
async def test_issue_zigbee_group_command(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
) -> None:
    """Test the group command action is sent to the group."""
    await setup_zha()
    group = get_zha_gateway(hass).get_group(FIXTURE_GRP_WITH_ENTITIES_ID)
    cluster = group.endpoint[general.OnOff.cluster_id]

    with patch.object(cluster, "command", AsyncMock()) as command_mock:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ISSUE_ZIGBEE_GROUP_COMMAND,
            GROUP_COMMAND_DATA,
            blocking=True,
        )

    assert command_mock.await_args_list == [
        call(0, manufacturer=ZIGPY_UNDEFINED, expect_reply=True)
    ]


@pytest.mark.usefixtures("zigpy_app_controller_with_group")
async def test_issue_zigbee_group_command_radio_failure(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
) -> None:
    """Test a radio failure in the group command action raises a readable error."""
    await setup_zha()
    group = get_zha_gateway(hass).get_group(FIXTURE_GRP_WITH_ENTITIES_ID)
    cluster = group.endpoint[general.OnOff.cluster_id]

    with (
        patch.object(
            cluster,
            "command",
            side_effect=DeliveryError("Failed to deliver packet", 225),
        ),
        pytest.raises(
            HomeAssistantError,
            match="Failed to send request: Failed to deliver packet",
        ),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ISSUE_ZIGBEE_GROUP_COMMAND,
            GROUP_COMMAND_DATA,
            blocking=True,
        )


async def test_issue_zigbee_group_command_unknown_group_raises(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
) -> None:
    """Test the group command action rejects an unknown group."""
    await setup_zha()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ISSUE_ZIGBEE_GROUP_COMMAND,
            {**GROUP_COMMAND_DATA, "group": 1234},
            blocking=True,
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "group_not_found"
    assert err.value.translation_placeholders == {"group_id": "1234"}


@pytest.mark.parametrize(
    ("data", "method"),
    [
        pytest.param({}, "permit", id="permit"),
        pytest.param(
            {
                "source_ieee": IEEE_SWITCH_DEVICE,
                "install_code": "52797BF4A5084DAA8E1712B61741CA024051",
            },
            "permit_with_link_key",
            id="install_code",
        ),
        pytest.param(
            {"qr_code": "Z:000D6FFFFED4163B$I:52797BF4A5084DAA8E1712B61741CA024051"},
            "permit_with_link_key",
            id="qr_code",
        ),
    ],
)
async def test_permit_radio_failure(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_app_controller: ControllerApplication,
    data: dict,
    method: str,
) -> None:
    """Test a radio failure in the permit action raises a readable error."""
    await setup_zha()

    with (
        patch.object(
            zigpy_app_controller,
            method,
            side_effect=DeliveryError("Failed to deliver packet", 225),
        ),
        pytest.raises(
            HomeAssistantError,
            match="Failed to send request: Failed to deliver packet",
        ),
    ):
        await hass.services.async_call(DOMAIN, SERVICE_PERMIT, data, blocking=True)


async def test_remove_radio_failure(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
) -> None:
    """Test a radio failure in the remove action raises a readable error."""
    await setup_zha()
    gateway = get_zha_gateway(hass)

    with (
        patch.object(
            gateway,
            "async_remove_device",
            side_effect=DeliveryError("Failed to deliver packet", 225),
        ),
        pytest.raises(
            HomeAssistantError,
            match="Failed to send request: Failed to deliver packet",
        ),
    ):
        await hass.services.async_call(
            DOMAIN, SERVICE_REMOVE, {"ieee": IEEE_SWITCH_DEVICE}, blocking=True
        )


@pytest.mark.parametrize(
    ("service", "data"),
    [
        pytest.param(
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            SET_ATTRIBUTE_DATA,
            id="set_zigbee_cluster_attribute",
        ),
        pytest.param(
            SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
            CLUSTER_COMMAND_DATA,
            id="issue_zigbee_cluster_command",
        ),
    ],
)
async def test_cluster_service_green_power_device_raises(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    service: str,
    data: dict,
) -> None:
    """Test the cluster actions reject a Green Power device."""
    await setup_zha()
    gateway = get_zha_gateway(hass)

    zigpy_gpd = ZigpyGreenPowerDevice(
        gateway.application_controller,
        application_id=ApplicationID.SrcID,
        src_id=SrcID(0x12345678),
    )
    gateway.application_controller.devices[zigpy_gpd.ieee] = zigpy_gpd
    gateway.get_or_create_device(zigpy_gpd)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, service, {"ieee": str(zigpy_gpd.ieee), **data}, blocking=True
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "not_zigbee_device"
    assert err.value.translation_placeholders == {"ieee": str(zigpy_gpd.ieee)}
