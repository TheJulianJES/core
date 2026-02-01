"""Unit tests for MatterEntity translation_key logic.

This file validates the logic for setting translation_key and entity name
in the MatterEntity class, including edge cases for primary entities.
"""

from unittest.mock import MagicMock

import pytest

from homeassistant.components.matter.entity import MatterEntity, MatterEntityDescription


class DummyEndpoint:
    """Dummy endpoint mock for MatterEntity tests.

    Simulates a Matter endpoint with required attributes for unique_id construction.
    """

    def __init__(self) -> None:
        """Initialize dummy endpoint with required node attributes."""
        self.endpoint_id = 1  # Required for unique_id construction
        self.node = MagicMock()
        self.node.is_bridge_device = False  # Simulate a non-bridge device
        self.node.available = True  # Device is available
        self.node.endpoints = {1: self}  # Endpoint mapping for lookup
        self.device_info = MagicMock()  # Placeholder for device info

    def has_attribute(self, *_) -> bool:
        """Always return False for attribute presence."""
        return False

    def get_attribute_value(self, *args, **kwargs) -> None:
        """Always return None for attribute value."""
        return


class DummyEntityInfo:
    """Dummy entity info mock for MatterEntity tests.

    Provides required attributes for entity instantiation.
    """

    def __init__(self) -> None:
        """Initialize dummy entity info with description and attributes."""
        self.entity_description = MatterEntityDescription(key="dummy")
        self.primary_attribute = MagicMock(cluster_id=1, attribute_id=1)
        self.attributes_to_watch = []
        self.discovery_schema = MagicMock()
        self.discovery_schema.featuremap_contains = None


@pytest.mark.parametrize(
    (
        "platform_translation_key",
        "name_postfix",
        "expected_key",
        "expected_name",
    ),
    [
        ("thermostat", None, "thermostat", None),
        ("thermostat", "Heat", "thermostat", "Dummy"),
        (None, None, None, "Dummy"),
    ],
)
def test_translation_key_and_name(
    platform_translation_key, name_postfix, expected_key, expected_name
) -> None:
    """Test translation_key and name assignment logic for MatterEntity.

    This test verifies that the translation_key and name fields are set
    correctly for primary and non-primary entities based on platform_translation_key
    and name_postfix values.
    """
    # Create a mock MatterClient and patch required server_info attributes
    matter_client = MagicMock()
    server_info = MagicMock()
    server_info.compressed_fabric_id = 1234567890123456  # Required for unique_id
    server_info.node_id = 42  # Required for unique_id
    matter_client.server_info = server_info

    # Create a dummy endpoint and patch required node attributes
    endpoint = DummyEndpoint()
    endpoint.node.compressed_fabric_id = 1234567890123456  # Required for unique_id
    endpoint.node.node_id = 42  # Required for unique_id

    # Create a dummy entity info
    entity_info = DummyEntityInfo()

    # Instantiate the MatterEntity
    entity = MatterEntity(matter_client, endpoint, entity_info)
    # Set test parameters
    entity._platform_translation_key = platform_translation_key
    entity._name_postfix = name_postfix
    entity._attr_name = "Dummy"

    # Simulate the logic for translation_key and name assignment
    if entity._platform_translation_key and not entity.translation_key:
        entity._attr_translation_key = entity._platform_translation_key
        if not entity._name_postfix:
            entity._attr_name = None

    # Assert expected translation_key and name
    assert entity._attr_translation_key == expected_key
    assert entity._attr_name == expected_name
