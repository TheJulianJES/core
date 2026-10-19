"""Benchmark the Matter device topology for multi-endpoint devices.

This captures, per fixture, the node device, any child devices, bridge
(``via_device``) links and the entity names on each device. It is a benchmark:
changes to how multi-endpoint devices are represented (e.g. splitting endpoints
into child devices) show up directly as a diff of the snapshot.
"""

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .common import snapshot_matter_devices


@pytest.mark.usefixtures("matter_node")
@pytest.mark.parametrize(
    "node_fixture",
    [
        # A dual-socket plug: two On/Off Plug-in Unit endpoints of the same type.
        "eve_energy_20ecn4101",
        # Multiple endpoints sharing a primary attribute but of different device
        # types (a dimmer with an extra color endpoint).
        "inovelli_vtm31",
        # Many Generic Switch (button) endpoints.
        "haojai_switch",
        # A window covering plus two Generic Switch endpoints, carrying semantic
        # tags.
        "aqara_shutter_switch_h2",
        # Bridges: bridged devices are separate HA devices linked via via_device.
        "mock_composed_bridge",
        "fritz_bridge",
        # Nested composed devices (appliances): part endpoints collapse onto their
        # compose parent's device.
        "silabs_refrigerator",
        "mock_air_purifier",
        # Further fixtures with more than one non-root endpoint on the node
        # device, or bridges, whose topology a child device split could change.
        "aqara_door_window_p2",
        "aqara_hub_m3",
        "aqara_light_switch_h2_us_4099",
        "aqara_light_switch_h2_us_4100",
        "aqara_light_switch_h2_us_4101",
        "aqara_motion_p2",
        "aqara_multi_state_p100",
        "aqara_presence_fp300",
        "aqara_sensor_w100",
        "aqara_smart_lock_u300",
        "aqara_smart_video_doorbell_g410",
        "aqara_thermostat_w500",
        "aqara_thermostat_w500_presets",
        "aqara_u200",
        "atios_ade_r12",
        "atios_knx_bridge",
        "cync_keypad_dimmer",
        "dewenwils_3_zone_transformer",
        "ductech_smartwings_levitate",
        "eve_energy_20ebu4101",
        "eve_energy_20ecl1301",
        "eve_energy_plug_patched",
        "eve_motion_20eby9901",
        "eve_weather_sensor",
        "hager_neva",
        "heiman_motion_sensor_m1",
        "ikea_bilresa_dual_button",
        "ikea_dirigera",
        "ikea_grillplats_plug",
        "ikea_myggspray_wrlss_mtn_sensor",
        "ikea_scroll_wheel",
        "ikea_timmerflotte_temp_hmd_sensor",
        "inovelli_vtm30",
        "inovelli_vtm36",
        "lifx_switch_115",
        "lifx_switch_89",
        "localizedtech_presence_one",
        "matterbridge_aggregator",
        "meross_smart_presence_sensor",
        "meross_smart_thermostat_57346",
        "mock_dimmable_light",
        "mock_generic_switch_multi",
        "mock_leak_sensor",
        "nanoleaf_nl77k1",
        "nature_remo_nano",
        "neo_smart_blinds_c_br300",
        "ohf_zigbee_dali_bridge",
        "pergolux_pergola_controller",
        "rollease_acmeda_pulse_pro",
        "selve_home_server_3",
        "silabs_dishwasher",
        "silabs_laundrywasher",
        "silabs_light_switch",
        "silabs_range_hood",
        "switchbot_air_purifier_2033",
        "switchbot_air_purifier_2036",
        "switchbot_hub_mini",
        "switchbot_relay_switch_2pm",
        "tapo_smart_outdoor_plug_in_dimmer",
        "tapo_smart_wi_fi_outlet",
        "tapo_smart_wi_fi_plug_265",
        "tapo_smart_wi_fi_power_strip",
        "tasmota_generic",
        "thirdreality_smart_color_night_light",
        "tp_link_smart_outdoor_plug",
        "tuya_smart_wired_gateway_pro",
        "uascent_smart_switch",
        "v1_ir_blaster",
        "wago_home_blind_control",
        "wago_home_relay_16a",
        "zemismart_m1_hub",
    ],
)
async def test_multi_endpoint_device_topology(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Benchmark devices, child devices and entity names for multi-endpoint devices."""
    snapshot_matter_devices(hass, device_registry, entity_registry, snapshot)
