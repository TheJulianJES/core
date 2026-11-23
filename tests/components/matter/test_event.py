"""Test Matter Event entities."""

from typing import Any
from unittest.mock import MagicMock

from matter_server.client.models.node import MatterNode
from matter_server.common.models import EventType, MatterNodeEvent
import pytest
from pytest_homeassistant.common import async_capture_events
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.event import (
    ATTR_PRESS_COUNT,
    EventEntityCapabilityAttribute,
    EventEntityStateAttribute,
)
from homeassistant.const import EVENT_STATE_CHANGED, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .common import (
    set_node_attribute,
    snapshot_matter_entities,
    trigger_subscription_callback,
)


async def trigger_switch_event(
    hass: HomeAssistant,
    matter_client: MagicMock,
    node: MatterNode,
    event_id: int,
    data: dict[str, Any] | None = None,
    endpoint_id: int = 1,
    cluster_id: int = 59,
    event_number: int = 0,
) -> None:
    """Trigger a Switch cluster event on the given node."""
    await trigger_subscription_callback(
        hass,
        matter_client,
        EventType.NODE_EVENT,
        MatterNodeEvent(
            node_id=node.node_id,
            endpoint_id=endpoint_id,
            cluster_id=cluster_id,
            event_id=event_id,
            event_number=event_number,
            priority=1,
            timestamp=0,
            timestamp_type=0,
            data=data,
        ),
    )


@pytest.fixture
def platform() -> Platform:
    """Return the platform under test."""
    return Platform.EVENT


@pytest.mark.usefixtures("matter_devices")
async def test_events(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test events."""
    snapshot_matter_entities(hass, entity_registry, snapshot, Platform.EVENT)


@pytest.mark.parametrize("node_fixture", ["mock_generic_switch"])
async def test_generic_switch_node(
    hass: HomeAssistant,
    matter_client: MagicMock,
    matter_node: MatterNode,
) -> None:
    """Test event entity for a GenericSwitch node."""
    state = hass.states.get("event.mock_generic_switch_button")
    assert state
    assert state.state == "unknown"
    assert state.name == "Mock Generic Switch Button"
    # check event_types from featuremap 14 (0b1110)
    assert state.attributes[EventEntityCapabilityAttribute.EVENT_TYPES] == [
        "press_start",
        "press_end",
        "long_press_start",
        "long_press_end",
    ]
    # trigger firing an InitialPress event from the device
    await trigger_switch_event(hass, matter_client, matter_node, 1)
    state = hass.states.get("event.mock_generic_switch_button")
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "press_start"
    # trigger firing a ShortRelease event from the device
    await trigger_switch_event(hass, matter_client, matter_node, 3)
    state = hass.states.get("event.mock_generic_switch_button")
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "press_end"
    # event types are recomputed when the feature map changes,
    # here multi press support is added (featuremap 30)
    set_node_attribute(matter_node, 1, 59, 65532, 30)
    await trigger_subscription_callback(
        hass, matter_client, data=(matter_node.node_id, "1/59/65532", 30)
    )
    state = hass.states.get("event.mock_generic_switch_button")
    assert state.attributes[EventEntityCapabilityAttribute.EVENT_TYPES] == [
        "press_start",
        "press_end",
        "long_press_start",
        "long_press_end",
        "single_press",
        "multi_press",
    ]


@pytest.mark.parametrize("node_fixture", ["mock_generic_switch"])
async def test_legacy_event_entity_disabled_by_default(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    matter_node: MatterNode,
) -> None:
    """Test the legacy event entity is registered but disabled by default."""
    assert hass.states.get("event.mock_generic_switch_button_deprecated") is None
    entity_entry = entity_registry.async_get(
        "event.mock_generic_switch_button_deprecated"
    )
    assert entity_entry
    assert entity_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entity_entry.unique_id.endswith("-GenericSwitch-59-1")
    assert entity_entry.original_name == "Button (deprecated)"


@pytest.mark.parametrize(
    (
        "node_fixture",
        "entity_id",
        "expected_event_types",
        "event_id",
        "event_data",
        "expected_event_type",
    ),
    [
        pytest.param(
            "mock_generic_switch",
            "event.mock_generic_switch_button_deprecated",
            ["initial_press", "short_release", "long_press", "long_release"],
            1,
            None,
            "initial_press",
            id="momentary",
        ),
        pytest.param(
            "mock_generic_switch_multi",
            "event.mock_generic_switch_button_deprecated_1",
            ["multi_press_1", "multi_press_2", "long_press", "long_release"],
            6,
            {"totalNumberOfPressesCounted": 2},
            "multi_press_2",
            id="multi_press",
        ),
    ],
)
async def test_legacy_event_entity(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    matter_client: MagicMock,
    matter_node: MatterNode,
    entity_id: str,
    expected_event_types: list[str],
    event_id: int,
    event_data: dict[str, Any] | None,
    expected_event_type: str,
) -> None:
    """Test the legacy event entity still works when enabled."""
    entity_registry.async_update_entity(entity_id, disabled_by=None)
    config_entry = hass.config_entries.async_entries("matter")[0]
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state
    assert (
        state.attributes[EventEntityCapabilityAttribute.EVENT_TYPES]
        == expected_event_types
    )
    # trigger firing an event from the device
    await trigger_switch_event(hass, matter_client, matter_node, event_id, event_data)
    state = hass.states.get(entity_id)
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == expected_event_type


@pytest.mark.parametrize("node_fixture", ["mock_generic_switch_multi"])
async def test_generic_switch_multi_node(
    hass: HomeAssistant,
    matter_client: MagicMock,
    matter_node: MatterNode,
) -> None:
    """Test event entity for a GenericSwitch node with multiple buttons."""
    state_button_1 = hass.states.get("event.mock_generic_switch_button_1")
    assert state_button_1
    assert state_button_1.state == "unknown"
    # name should be 'DeviceName Button (1)'
    assert state_button_1.name == "Mock Generic Switch Button (1)"
    # check event_types from featuremap 30 (0b11110)
    assert state_button_1.attributes[EventEntityCapabilityAttribute.EVENT_TYPES] == [
        "press_start",
        "press_end",
        "long_press_start",
        "long_press_end",
        "single_press",
        "multi_press",
    ]
    # check button 2
    state_button_2 = hass.states.get("event.mock_generic_switch_button_fancy_button")
    assert state_button_2
    assert state_button_2.state == "unknown"
    # name should be 'DeviceName Button (Fancy Button)' due to
    # ha_entitylabel 'Fancy Button'
    assert state_button_2.name == "Mock Generic Switch Button (Fancy Button)"

    # a MultiPressOngoing event from the device is ignored
    await trigger_switch_event(
        hass, matter_client, matter_node, 5, {"currentNumberOfPressesCounted": 2}
    )
    state = hass.states.get("event.mock_generic_switch_button_1")
    assert state.state == "unknown"

    # trigger firing a MultiPressComplete event from the device
    await trigger_switch_event(
        hass, matter_client, matter_node, 6, {"totalNumberOfPressesCounted": 3}
    )
    state = hass.states.get("event.mock_generic_switch_button_1")
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "multi_press"
    assert state.attributes[ATTR_PRESS_COUNT] == 3

    # a MultiPressComplete event with a single press is a single_press
    await trigger_switch_event(
        hass, matter_client, matter_node, 6, {"totalNumberOfPressesCounted": 1}
    )
    state = hass.states.get("event.mock_generic_switch_button_1")
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "single_press"
    assert ATTR_PRESS_COUNT not in state.attributes
    last_changed = state.last_changed

    # a MultiPressComplete event for an aborted sequence is ignored
    await trigger_switch_event(
        hass, matter_client, matter_node, 6, {"totalNumberOfPressesCounted": 0}
    )
    # a MultiPressComplete event without a count is ignored as well
    await trigger_switch_event(hass, matter_client, matter_node, 6)
    state = hass.states.get("event.mock_generic_switch_button_1")
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "single_press"
    assert state.last_changed == last_changed


@pytest.mark.parametrize("node_fixture", ["mock_generic_switch"])
@pytest.mark.parametrize("attributes", [{"1/59/65532": 2}])
async def test_momentary_switch_without_release(
    hass: HomeAssistant,
    matter_client: MagicMock,
    matter_node: MatterNode,
) -> None:
    """Test event entity for a momentary switch without release support."""
    state = hass.states.get("event.mock_generic_switch_button")
    assert state
    # a switch with featuremap 2 (0b10) only emits InitialPress,
    # which is sent when the button goes down
    assert state.attributes[EventEntityCapabilityAttribute.EVENT_TYPES] == [
        "press_start"
    ]
    # trigger firing an InitialPress event from the device
    await trigger_switch_event(hass, matter_client, matter_node, 1)
    state = hass.states.get("event.mock_generic_switch_button")
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "press_start"


@pytest.mark.parametrize("node_fixture", ["mock_doorbell"])
async def test_doorbell_node(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    matter_node: MatterNode,
) -> None:
    """Test a doorbell node does not get any event entity for now."""
    # a doorbell endpoint that does not also advertise the GenericSwitch device
    # type gets no event entity until a dedicated doorbell entity is added
    assert hass.states.async_entity_ids("event") == []
    assert entity_registry.async_get("event.mock_doorbell_button") is None
    assert entity_registry.async_get("event.mock_doorbell_button_deprecated") is None


@pytest.mark.parametrize("node_fixture", ["mock_latching_switch"])
async def test_latching_switch_node(
    hass: HomeAssistant,
    matter_client: MagicMock,
    matter_node: MatterNode,
) -> None:
    """Test event entity for a latching switch node."""
    state = hass.states.get("event.mock_latching_switch_button")
    assert state
    assert state.state == "unknown"
    # check event_types from featuremap 1 (0b1)
    assert state.attributes[EventEntityCapabilityAttribute.EVENT_TYPES] == [
        "switch_latched"
    ]
    # trigger firing a SwitchLatched event from the device
    await trigger_switch_event(hass, matter_client, matter_node, 0, {"newPosition": 1})
    state = hass.states.get("event.mock_latching_switch_button")
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "switch_latched"
    assert state.attributes["newPosition"] == 1


@pytest.mark.parametrize("node_fixture", ["mock_action_switch"])
async def test_action_switch_node(
    hass: HomeAssistant,
    matter_client: MagicMock,
    matter_node: MatterNode,
) -> None:
    """Test event entity for an action switch node."""
    state = hass.states.get("event.mock_action_switch_button")
    assert state
    assert state.state == "unknown"
    # check event_types from featuremap 58 (0b111010):
    # an action switch does not emit ShortRelease and MultiPressOngoing events,
    # and it sends InitialPress only once per multi-press sequence
    assert state.attributes[EventEntityCapabilityAttribute.EVENT_TYPES] == [
        "long_press_start",
        "long_press_end",
        "single_press",
        "multi_press",
        "initial_press",
    ]
    # an InitialPress event is passed on as the Matter-specific initial_press
    await trigger_switch_event(hass, matter_client, matter_node, 1)
    state = hass.states.get("event.mock_action_switch_button")
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "initial_press"
    last_changed = state.last_changed
    # a ShortRelease event from the device should be ignored
    await trigger_switch_event(hass, matter_client, matter_node, 3)
    # a MultiPressOngoing event from the device should be ignored
    await trigger_switch_event(
        hass, matter_client, matter_node, 5, {"currentNumberOfPressesCounted": 2}
    )
    # an unknown event id from the device should be ignored
    await trigger_switch_event(hass, matter_client, matter_node, 7)
    # an event from another cluster should be ignored
    await trigger_switch_event(hass, matter_client, matter_node, 1, cluster_id=8)
    state = hass.states.get("event.mock_action_switch_button")
    assert state.last_changed == last_changed
    # trigger firing a MultiPressComplete event from the device
    await trigger_switch_event(
        hass, matter_client, matter_node, 6, {"totalNumberOfPressesCounted": 4}
    )
    state = hass.states.get("event.mock_action_switch_button")
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "multi_press"
    assert state.attributes[ATTR_PRESS_COUNT] == 4


@pytest.mark.parametrize("node_fixture", ["ikea_scroll_wheel"])
async def test_scroll_wheel_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    matter_client: MagicMock,
    matter_node: MatterNode,
) -> None:
    """Test the event entities of a scroll wheel's rotary and button endpoints."""
    entries = {
        entry.unique_id.split("-MatterNodeDevice-", 1)[1]: entry
        for entry in er.async_entries_for_config_entry(
            entity_registry,
            hass.config_entries.async_entries("matter")[0].entry_id,
        )
        if entry.domain == "event"
    }
    # each layer gets a rotary entity on its clockwise (Switches.Up) endpoint,
    # which also covers the counter-clockwise (Switches.Down) endpoint
    for up_endpoint_id, down_endpoint_id in ((1, 2), (4, 5), (7, 8)):
        rotary = entries[f"{up_endpoint_id}-GenericSwitchRotary-59-1"]
        assert rotary.disabled_by is None
        assert rotary.original_device_class is None
        assert f"{down_endpoint_id}-GenericSwitchRotary-59-1" not in entries
    # the rotary endpoints get no standard button entity,
    # and their legacy entity is deprecated
    for endpoint_id in (1, 2, 4, 5, 7, 8):
        assert f"{endpoint_id}-GenericSwitchButton-59-1" not in entries
        legacy = entries[f"{endpoint_id}-GenericSwitch-59-1"]
        assert legacy.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert legacy.translation_key == "button_deprecated"
    # the push button endpoints get the standard button entity
    # and the deprecated legacy entity, but no rotary entity
    for endpoint_id in (3, 6, 9):
        assert entries[f"{endpoint_id}-GenericSwitchButton-59-1"].disabled_by is None
        legacy = entries[f"{endpoint_id}-GenericSwitch-59-1"]
        assert legacy.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert f"{endpoint_id}-GenericSwitchRotary-59-1" not in entries

    # the push button endpoint maps multi-presses to the standard types
    entity_id = entries["3-GenericSwitchButton-59-1"].entity_id
    await trigger_switch_event(
        hass,
        matter_client,
        matter_node,
        6,
        {"totalNumberOfPressesCounted": 3},
        endpoint_id=3,
    )
    state = hass.states.get(entity_id)
    assert state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "multi_press"
    assert state.attributes[ATTR_PRESS_COUNT] == 3


@pytest.mark.parametrize("node_fixture", ["ikea_scroll_wheel"])
async def test_scroll_wheel_rotation(
    hass: HomeAssistant,
    matter_client: MagicMock,
    matter_node: MatterNode,
) -> None:
    """Test the rotary entity reports the steps turned since the last report."""
    entity_id = "event.bilresa_scroll_wheel_rotary_1"
    state = hass.states.get(entity_id)
    assert state
    assert state.state == "unknown"
    assert state.name == "BILRESA scroll wheel Rotary (1)"
    assert state.attributes[EventEntityCapabilityAttribute.EVENT_TYPES] == ["rotation"]
    other_layer_entity_id = "event.bilresa_scroll_wheel_rotary_4"
    state_changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    event_number = 100

    async def send(
        endpoint_id: int,
        event_id: int,
        data: dict[str, Any] | None = None,
        *,
        replay: bool = False,
    ) -> None:
        nonlocal event_number
        if not replay:
            event_number += 1
        await trigger_switch_event(
            hass,
            matter_client,
            matter_node,
            event_id,
            data,
            endpoint_id=endpoint_id,
            event_number=event_number,
        )

    def rotations(entity: str = entity_id) -> list[tuple[str, int]]:
        """Return the rotations of an entity reported since the last call."""
        result = []
        for event in [e for e in state_changes if e.data["entity_id"] == entity]:
            state_changes.remove(event)
            new_state = event.data["new_state"]
            assert (
                new_state.attributes[EventEntityStateAttribute.EVENT_TYPE] == "rotation"
            )
            result.append(
                (new_state.attributes["direction"], new_state.attributes["steps"])
            )
        return result

    # a one-detent turn: InitialPress and ShortRelease carry no amount,
    # the detent is reported by MultiPressComplete
    await send(1, 1)
    await send(1, 3)
    assert rotations() == []
    await send(1, 6, {"totalNumberOfPressesCounted": 1})
    assert rotations() == [("clockwise", 1)]

    # a longer turn: each report carries the running count of the sequence,
    # the entity reports the steps since the previous report of that endpoint,
    # and the completion after the wheel stopped adds no steps
    await send(1, 1)
    await send(1, 5, {"currentNumberOfPressesCounted": 3})
    await send(2, 5, {"currentNumberOfPressesCounted": 2})
    await send(1, 5, {"currentNumberOfPressesCounted": 7})
    await send(2, 5, {"currentNumberOfPressesCounted": 5})
    await send(1, 6, {"totalNumberOfPressesCounted": 7})
    await send(2, 6, {"totalNumberOfPressesCounted": 6})
    assert rotations() == [
        ("clockwise", 3),
        ("counter_clockwise", 2),
        ("clockwise", 4),
        ("counter_clockwise", 3),
        ("counter_clockwise", 1),
    ]

    # the device starts a new sequence once MultiPressMax is reached
    # while the wheel keeps turning
    await send(2, 5, {"currentNumberOfPressesCounted": 16})
    await send(2, 6, {"totalNumberOfPressesCounted": 18})
    await send(2, 5, {"currentNumberOfPressesCounted": 3})
    await send(2, 6, {"totalNumberOfPressesCounted": 4})
    assert rotations() == [
        ("counter_clockwise", 16),
        ("counter_clockwise", 2),
        ("counter_clockwise", 3),
        ("counter_clockwise", 1),
    ]

    # a new sequence after a missed completion is reported in full
    await send(1, 5, {"currentNumberOfPressesCounted": 9})
    await send(1, 5, {"currentNumberOfPressesCounted": 2})
    await send(1, 6, {"totalNumberOfPressesCounted": 4})
    assert rotations() == [("clockwise", 9), ("clockwise", 2), ("clockwise", 2)]
    await send(1, 5, {"currentNumberOfPressesCounted": 2})
    await send(1, 5, {"currentNumberOfPressesCounted": 2})
    await send(1, 6, {"totalNumberOfPressesCounted": 2})
    assert rotations() == [("clockwise", 2), ("clockwise", 2)]

    # an event replayed after a resubscription (same event number) is ignored,
    # as is an aborted sequence or one without a count
    await send(1, 5, {"currentNumberOfPressesCounted": 5})
    await send(1, 6, {"totalNumberOfPressesCounted": 5})
    await send(1, 6, {"totalNumberOfPressesCounted": 5}, replay=True)
    await send(1, 6, {"totalNumberOfPressesCounted": 0})
    await send(1, 6)
    await send(1, 5, {"currentNumberOfPressesCounted": 2})
    assert rotations() == [("clockwise", 5), ("clockwise", 2)]

    # events of another layer's endpoints are handled by that layer's entity
    await send(4, 6, {"totalNumberOfPressesCounted": 5})
    await send(5, 6, {"totalNumberOfPressesCounted": 2})
    assert rotations() == []
    assert rotations(other_layer_entity_id) == [
        ("clockwise", 5),
        ("counter_clockwise", 2),
    ]
    assert not state_changes


@pytest.mark.parametrize("node_fixture", ["ikea_scroll_wheel"])
@pytest.mark.parametrize(
    "attributes",
    [
        {
            # endpoint 4 claims the layer of endpoints 1 and 2 as well
            "4/29/4": [
                {"0": None, "1": 8, "2": 6, "3": "1"},
                {"0": None, "1": 67, "2": 3},
                {"0": None, "1": 67, "2": 8, "3": "rotary"},
            ]
        }
    ],
)
async def test_scroll_wheel_ambiguous_layer(
    hass: HomeAssistant,
    matter_client: MagicMock,
    matter_node: MatterNode,
) -> None:
    """Test a counter-clockwise endpoint is not paired if the layer is ambiguous."""
    state_changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    await trigger_switch_event(
        hass,
        matter_client,
        matter_node,
        6,
        {"totalNumberOfPressesCounted": 2},
        endpoint_id=2,
        event_number=1,
    )
    assert not state_changes
    await trigger_switch_event(
        hass,
        matter_client,
        matter_node,
        6,
        {"totalNumberOfPressesCounted": 2},
        endpoint_id=1,
        event_number=2,
    )
    assert [event.data["entity_id"] for event in state_changes] == [
        "event.bilresa_scroll_wheel_rotary_1"
    ]
