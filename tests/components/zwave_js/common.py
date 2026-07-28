"""Provide common test tools for Z-Wave JS."""

from copy import deepcopy
from functools import cache
import json
from pathlib import Path
from typing import Any

from syrupy.assertion import SnapshotAssertion
from zwave_js_server.model.node.data_model import NodeDataType

from homeassistant.components.zwave_js.helpers import (
    ZwaveValueMatcher,
    value_matches_matcher,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# The controller node is always added by the `client` fixture, so node fixtures
# that also claim node id 1 can never be loaded alongside it.
CONTROLLER_NODE_ID = 1

AIR_TEMPERATURE_SENSOR = "sensor.multisensor_6_air_temperature"
BATTERY_SENSOR = "sensor.multisensor_6_battery_level"
TAMPER_SENSOR = "binary_sensor.multisensor_6_tampering_product_cover_removed"
HUMIDITY_SENSOR = "sensor.multisensor_6_humidity"
POWER_SENSOR = "sensor.smart_plug_with_two_usb_ports_value_electric_consumed"
ENERGY_SENSOR = "sensor.smart_plug_with_two_usb_ports_value_electric_consumed_2"
VOLTAGE_SENSOR = "sensor.smart_plug_with_two_usb_ports_value_electric_consumed_3"
CURRENT_SENSOR = "sensor.smart_plug_with_two_usb_ports_value_electric_consumed_4"
SWITCH_ENTITY = "switch.smart_plug_with_two_usb_ports"
ENABLED_LEGACY_BINARY_SENSOR = "binary_sensor.z_wave_door_window_sensor_any"
DISABLED_LEGACY_BINARY_SENSOR = "binary_sensor.multisensor_6_any"
NOTIFICATION_MOTION_BINARY_SENSOR = "binary_sensor.multisensor_6_motion_detection"
NOTIFICATION_MOTION_SENSOR = "sensor.multisensor_6_home_security_motion_sensor_status"
INDICATOR_SENSOR = "sensor.z_wave_thermostat_indicator_value"
BASIC_LIGHT_ENTITY = "light.livingroom_livingroomlight_basic"
PROPERTY_DOOR_STATUS_BINARY_SENSOR = (
    "binary_sensor.august_smart_lock_pro_3rd_gen_the_current_status_of_the_door"
)
CLIMATE_RADIO_THERMOSTAT_ENTITY = "climate.z_wave_thermostat"
CLIMATE_DANFOSS_LC13_ENTITY = "climate.living_connect_z_thermostat"
CLIMATE_EUROTRONICS_SPIRIT_Z_ENTITY = "climate.thermostatic_valve"
CLIMATE_FLOOR_THERMOSTAT_ENTITY = "climate.floor_thermostat"
CLIMATE_MAIN_HEAT_ACTIONNER = "climate.kitchen_main_heat_actionner"
CLIMATE_AIDOO_HVAC_UNIT_ENTITY = "climate.aidoo_control_hvac_unit"
BULB_6_MULTI_COLOR_LIGHT_ENTITY = "light.bulb_6_multi_color"
EATON_RF9640_ENTITY = "light.livingroom_allloaddimmer"
AEON_SMART_SWITCH_LIGHT_ENTITY = "light.smart_switch_6"
SCHLAGE_BE469_LOCK_ENTITY = "lock.touchscreen_deadbolt"
ZEN_31_ENTITY = "light.kitchen_kitchen_under_cabinet_lights"
METER_VOLTAGE_SENSOR = "sensor.smart_switch_6_electric_consumed_v"
HUMIDIFIER_ADC_T3000_ENTITY = "humidifier.adc_t3000_humidifier"
DEHUMIDIFIER_ADC_T3000_ENTITY = "humidifier.adc_t3000_dehumidifier"

PROPERTY_ULTRAVIOLET = "Ultraviolet"
TEST_SENSITIVE_NETWORK_KEY = "00112233445566778899AABBCCDDEEFF"


def replace_value_of_zwave_value(
    node_data: NodeDataType, matchers: list[ZwaveValueMatcher], new_value: Any
) -> NodeDataType:
    """Replace the value of a zwave value that matches the input matchers."""
    new_node_data = deepcopy(node_data)
    for value_data in new_node_data["values"]:
        for matcher in matchers:
            if value_matches_matcher(matcher, value_data):
                value_data["value"] = new_value

    return new_node_data


@cache
def get_node_state_fixtures() -> tuple[tuple[str, int], ...]:
    """Return every node state fixture as a (file name, node id) pair."""
    fixtures: list[tuple[str, int]] = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        # Distinguishes node states from diagnostics dumps and event payloads.
        if isinstance(data, dict) and "nodeId" in data and "values" in data:
            fixtures.append((path.name, data["nodeId"]))
    return tuple(fixtures)


@cache
def get_node_fixture_batches() -> tuple[tuple[str, ...], ...]:
    """Split the node state fixtures into batches with no duplicate node ids.

    Nodes are keyed by node id on the driver, so fixtures sharing a node id would
    silently overwrite each other if loaded together. Batching keeps the original
    node ids, which keeps entity unique ids stable as fixtures are added.

    The batch index is part of the snapshot names. A fixture with an unused node id
    is added to the first batch and leaves the other snapshots alone, but adding one
    that sorts before an existing fixture with the same node id moves that fixture to
    a later batch and renames its snapshots.

    Fixtures for node id 1 are skipped, so the two `nabu_casa_zwa2` controller node
    states are not covered here.
    """
    batches: list[dict[int, str]] = []
    for name, node_id in get_node_state_fixtures():
        if node_id == CONTROLLER_NODE_ID:
            continue
        for batch in batches:
            if node_id not in batch:
                batch[node_id] = name
                break
        else:
            batches.append({node_id: name})
    return tuple(tuple(batch.values()) for batch in batches)


def snapshot_zwave_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    config_entry_id: str,
    platform: Platform,
    fixture_names: dict[int, str],
) -> None:
    """Snapshot every entity of one platform, grouped by source node fixture.

    Entities that are disabled by default are not enabled for these tests, because
    some config parameter sensors carry both a unit and a list of states and fail to
    be added at all once enabled. Only their registry entry is snapshotted, so the
    disabled ones are still covered against unintended entity category or name
    changes.
    """
    entries = [
        entry
        for entry in er.async_entries_for_config_entry(entity_registry, config_entry_id)
        if entry.domain == platform
    ]
    for entry in entries:
        # Unique ids are `{home_id}.{node_id}-...` for value based entities and
        # `{home_id}.{node_id}.{suffix}` for the valueless ones.
        node_id = int(entry.unique_id.split(".")[1].split("-")[0])
        fixture_name = fixture_names[node_id]
        assert entry == snapshot(name=f"{fixture_name}][{entry.entity_id}-entry")
        if entry.disabled_by is None:
            assert hass.states.get(entry.entity_id) == snapshot(
                name=f"{fixture_name}][{entry.entity_id}-state"
            )
