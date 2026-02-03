"""Test Matter entity translation_key and name logic."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.components.matter.entity import MatterEntity, MatterEntityDescription
from homeassistant.helpers.typing import UNDEFINED


class DummyEndpoint:
    """Mock Matter endpoint."""

    def __init__(self) -> None:
        """Initialize endpoint."""
        self.endpoint_id = 1
        self.node = MagicMock()
        self.node.is_bridge_device = False
        self.node.available = True
        self.node.endpoints = {1: self}
        self.node.compressed_fabric_id = 1234567890123456
        self.node.node_id = 42
        self.device_info = MagicMock()

    def has_attribute(self, *_) -> bool:
        """Return False for attribute presence."""
        return False

    def get_attribute_value(self, *args, **kwargs) -> None:
        """Return None for attribute value."""
        return


class DummyEntityInfo:
    """Mock Matter entity info."""

    def __init__(self) -> None:
        """Initialize entity info."""
        self.entity_description = MatterEntityDescription(key="dummy")
        self.primary_attribute = MagicMock(cluster_id=1, attribute_id=1)
        self.attributes_to_watch = []
        self.discovery_schema = MagicMock()
        self.discovery_schema.platform_name = "dummy"
        self.discovery_schema.featuremap_contains = None


@pytest.mark.parametrize(
    (
        "platform_translation_key",
        "has_duplicate",
        "expect_translation_key",
        "expect_name_none",
        "expect_name_undefined",
    ),
    [
        ("thermostat", False, "thermostat", True, False),
        ("thermostat", True, "thermostat", False, False),
        (None, False, None, False, True),
    ],
)
def test_matter_entity_translation_key_and_name(
    platform_translation_key: str | None,
    has_duplicate: bool,
    expect_translation_key: str | None,
    expect_name_none: bool,
    expect_name_undefined: bool,
) -> None:
    """Test that translation_key and name are set correctly for Matter entities.

    Verifies that:
    - Primary entities (with platform_translation_key and no duplicate) have name=None
    - Non-primary entities (with postfix) keep their name
    - Entities without platform_translation_key don't have translation_key set
    """
    matter_client = MagicMock()
    server_info = MagicMock()
    server_info.compressed_fabric_id = 1234567890123456
    server_info.node_id = 42
    matter_client.server_info = server_info

    endpoint = DummyEndpoint()

    if has_duplicate:
        endpoint2 = DummyEndpoint()
        endpoint2.endpoint_id = 2
        endpoint.node.endpoints = {1: endpoint, 2: endpoint2}
        endpoint2.has_attribute = MagicMock(return_value=True)

    entity_info = DummyEntityInfo()

    class MatterEntityImpl(MatterEntity):
        _platform_translation_key = platform_translation_key

    entity = MatterEntityImpl(matter_client, endpoint, entity_info)

    if has_duplicate:
        platform_data = MagicMock()
        platform_data.platform_name = "matter"
        platform_data.domain = "climate"
        platform_data.platform_translations = {
            "component.matter.entity.climate.thermostat.name": "Thermostat"
        }
        platform_data.object_id_platform_translations = {}
        platform_data.component_translations = {}
        platform_data.object_id_component_translations = {}
        entity.platform_data = platform_data

    # Verify translation_key state via public property
    assert entity.translation_key == expect_translation_key

    # Verify name state via public property
    if expect_name_none:
        assert entity.name is None
    elif expect_name_undefined:
        assert entity.name is UNDEFINED
    else:
        # For non-primary entities, verify name is not explicitly None
        # (it could be derived from other properties)
        assert entity.name is not None
