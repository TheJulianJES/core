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
        "has_duplicate_attribute",
        "expected_key",
        "expected_name_is_none",
    ),
    [
        ("thermostat", False, "thermostat", True),
        ("thermostat", True, "thermostat", False),
        (None, False, None, False),
    ],
)
def test_translation_key_and_name(
    platform_translation_key,
    has_duplicate_attribute,
    expected_key,
    expected_name_is_none,
) -> None:
    """Test translation_key and name assignment logic for MatterEntity.

    This test verifies that the translation_key and name fields are set
    correctly for primary and non-primary entities based on platform_translation_key
    and has_duplicate_attribute values.
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
    endpoint.node.is_bridge_device = False

    # Create a second endpoint if we want to simulate duplicate attributes
    if has_duplicate_attribute:
        endpoint2 = DummyEndpoint()
        endpoint2.endpoint_id = 2
        endpoint.node.endpoints = {1: endpoint, 2: endpoint2}
        # The second endpoint should report having the attribute
        endpoint2.has_attribute = MagicMock(return_value=True)
    else:
        endpoint.node.endpoints = {1: endpoint}

    # Create a dummy entity info
    entity_info = DummyEntityInfo()

    # Create a temporary subclass with the specified platform_translation_key
    class MatterEntityImpl(MatterEntity):
        _platform_translation_key = platform_translation_key

    # Instantiate the entity
    entity = MatterEntityImpl(matter_client, endpoint, entity_info)

    # Verify state attributes are set correctly
    if expected_key is not None:
        assert hasattr(entity, "_attr_translation_key")
        assert entity._attr_translation_key == expected_key
    else:
        assert not hasattr(entity, "_attr_translation_key")

    if expected_name_is_none:
        # For primary entities, _attr_name should be explicitly set to None
        assert hasattr(entity, "_attr_name")
        assert entity._attr_name is None
    # For non-primary entities, we don't make assertions about name
    # since they may have other means of getting their name set
