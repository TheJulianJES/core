"""Matter cover."""

from dataclasses import dataclass
from enum import IntEnum
from math import floor
from typing import Any, override

from chip.clusters import Objects as clusters
from chip.clusters.ClusterObjects import ClusterAttributeDescriptor

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityDescription,
    CoverEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import LOGGER
from .entity import MatterEntity, MatterEntityDescription
from .helpers import MatterConfigEntry
from .models import MatterDiscoverySchema

# OperationalStatus packs three 2-bit fields: bits 1 to 0 hold the global
# state, bits 3 to 2 the lift state (conformance LF) and bits 5 to 4 the tilt
# state (conformance TL).
OPERATIONAL_STATUS_MASK = 0b11
LIFT_STATUS_SHIFT = 2
TILT_STATUS_SHIFT = 4

# Cover state is derived from both the operational status and the current
# position, which some devices report as separate attribute updates shortly
# after each other (e.g. stopped before the final position). Debounce state
# writes to avoid writing intermittent states.
STATE_WRITE_DEBOUNCE_COOLDOWN = 0.1

# map Matter window cover types to HA device class
TYPE_MAP = {
    clusters.WindowCovering.Enums.Type.kRollerShade: CoverDeviceClass.SHADE,
    clusters.WindowCovering.Enums.Type.kRollerShade2Motor: CoverDeviceClass.SHADE,
    clusters.WindowCovering.Enums.Type.kRollerShadeExterior: CoverDeviceClass.SHADE,
    clusters.WindowCovering.Enums.Type.kRollerShadeExterior2Motor: (
        CoverDeviceClass.SHADE
    ),
    clusters.WindowCovering.Enums.Type.kAwning: CoverDeviceClass.AWNING,
    clusters.WindowCovering.Enums.Type.kDrapery: CoverDeviceClass.CURTAIN,
    clusters.WindowCovering.Enums.Type.kTiltBlindTiltOnly: CoverDeviceClass.BLIND,
    clusters.WindowCovering.Enums.Type.kTiltBlindLiftAndTilt: CoverDeviceClass.BLIND,
}


class OperationalStatus(IntEnum):
    """Ongoing operations enumeration for coverings per Matter spec."""

    COVERING_IS_CURRENTLY_NOT_MOVING = 0b00
    COVERING_IS_CURRENTLY_OPENING = 0b01
    COVERING_IS_CURRENTLY_CLOSING = 0b10
    RESERVED = 0b11


# Each movement axis, as its OperationalStatus bit offset plus the attributes
# that tell whether that axis reached the position it was asked for.
MOVEMENT_AXES = (
    (
        LIFT_STATUS_SHIFT,
        clusters.WindowCovering.Bitmaps.Feature.kLift,
        clusters.WindowCovering.Attributes.CurrentPositionLiftPercent100ths,
        clusters.WindowCovering.Attributes.TargetPositionLiftPercent100ths,
    ),
    (
        TILT_STATUS_SHIFT,
        clusters.WindowCovering.Bitmaps.Feature.kTilt,
        clusters.WindowCovering.Attributes.CurrentPositionTiltPercent100ths,
        clusters.WindowCovering.Attributes.TargetPositionTiltPercent100ths,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MatterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Matter Cover from Config Entry."""
    matter = config_entry.runtime_data.adapter
    matter.register_platform_handler(Platform.COVER, async_add_entities)


@dataclass(frozen=True, kw_only=True)
class MatterCoverEntityDescription(CoverEntityDescription, MatterEntityDescription):
    """Describe Matter Cover entities."""


class MatterCover(MatterEntity, CoverEntity):
    """Representation of a Matter Cover."""

    _write_state_debounce_cooldown = STATE_WRITE_DEBOUNCE_COOLDOWN
    entity_description: MatterCoverEntityDescription

    @property
    @override
    def is_closed(self) -> bool | None:
        """Return true if cover is closed, None if no position."""
        if not self._entity_info.endpoint.has_attribute(
            None, clusters.WindowCovering.Attributes.CurrentPositionLiftPercent100ths
        ):
            return None

        return (
            self.current_cover_position == 0
            if self.current_cover_position is not None
            else None
        )

    @override
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover movement."""
        await self.send_device_command(clusters.WindowCovering.Commands.StopMotion())

    @override
    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self.send_device_command(clusters.WindowCovering.Commands.UpOrOpen())

    @override
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self.send_device_command(clusters.WindowCovering.Commands.DownOrClose())

    @override
    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set the cover to a specific position."""
        position = kwargs[ATTR_POSITION]
        await self.send_device_command(
            # value needs to be inverted and is sent in 100ths
            clusters.WindowCovering.Commands.GoToLiftPercentage((100 - position) * 100)
        )

    @override
    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the cover tilt to a specific position."""
        position = kwargs[ATTR_TILT_POSITION]
        await self.send_device_command(
            # value needs to be inverted and is sent in 100ths
            clusters.WindowCovering.Commands.GoToTiltPercentage((100 - position) * 100)
        )

    @callback
    def _axis_at_target(
        self,
        current_attribute: type[ClusterAttributeDescriptor],
        target_attribute: type[ClusterAttributeDescriptor],
    ) -> bool | None:
        """Return whether one axis rests at its target position.

        Returns None if the two positions cannot be compared, e.g. the axis is
        not position aware or a position is (still) unknown.
        """
        if not self._entity_info.endpoint.has_attribute(
            None, current_attribute
        ) or not self._entity_info.endpoint.has_attribute(None, target_attribute):
            return None
        current_position = self.get_matter_attribute_value(current_attribute)
        target_position = self.get_matter_attribute_value(target_attribute)
        if current_position is None or target_position is None:
            return None
        return bool(current_position == target_position)

    @callback
    def _positions_at_target(self) -> bool | None:
        """Return whether all supported positions match their target position.

        Returns None if the positions cannot be compared, e.g. the device is
        not position aware or a position is (still) unknown.
        """
        at_target: bool | None = None
        for _, _, current_attribute, target_attribute in MOVEMENT_AXES:
            if not self._entity_info.endpoint.has_attribute(
                None, current_attribute
            ) or not self._entity_info.endpoint.has_attribute(None, target_attribute):
                continue
            axis_at_target = self._axis_at_target(current_attribute, target_attribute)
            # a null position is unknown, not known to be at its target
            if axis_at_target is None:
                return None
            if not axis_at_target:
                return False
            at_target = True
        return at_target

    @callback
    def _has_unobservable_axis(self) -> bool:
        """Return whether a supported axis cannot report its position.

        Lift and tilt are independently position aware (PA_LF conformance
        `[LF]`, PA_TL conformance `[TL]`), so a covering may support an axis
        while being unable to report where that axis is.
        """
        feature_map = self.get_matter_attribute_value(
            clusters.WindowCovering.Attributes.FeatureMap
        )
        features = clusters.WindowCovering.Bitmaps.Feature
        return bool(
            (
                feature_map & features.kLift
                and not feature_map & features.kPositionAwareLift
            )
            or (
                feature_map & features.kTilt
                and not feature_map & features.kPositionAwareTilt
            )
        )

    @callback
    def _movement_state(self, operational_status: int) -> int:
        """Return the state the covering is actually moving in.

        Some devices report a moving operational status together with the
        final position(s) without a subsequent report clearing the moving
        state, leaving the cover stuck in a moving state. A covering resting
        at the position it was asked for is not moving.
        """
        feature_map = self.get_matter_attribute_value(
            clusters.WindowCovering.Attributes.FeatureMap
        )
        axis_state: int = OperationalStatus.COVERING_IS_CURRENTLY_NOT_MOVING
        axis_reported = False
        for shift, feature, current_attribute, target_attribute in MOVEMENT_AXES:
            # an axis the covering does not have reports no state of its own
            if not feature_map & feature:
                continue
            state = (operational_status >> shift) & OPERATIONAL_STATUS_MASK
            if not state:
                continue
            axis_reported = True
            if self._axis_at_target(current_attribute, target_attribute):
                continue
            axis_state = state

        if axis_reported:
            return axis_state

        # devices that populate only the global field cannot be checked per
        # axis, so keep the global state unless every supported axis is
        # observable and known to rest at its target
        if self._has_unobservable_axis() or not self._positions_at_target():
            return operational_status & OPERATIONAL_STATUS_MASK
        return OperationalStatus.COVERING_IS_CURRENTLY_NOT_MOVING

    @callback
    @override
    def _update_from_device(self) -> None:
        """Update from device."""
        operational_status = self.get_matter_attribute_value(
            clusters.WindowCovering.Attributes.OperationalStatus
        )

        assert operational_status is not None

        LOGGER.debug(
            "Operational status %s for %s",
            f"{operational_status:#010b}",
            self.entity_id,
        )

        state = self._movement_state(operational_status)
        match state:
            case OperationalStatus.COVERING_IS_CURRENTLY_OPENING:
                self._attr_is_opening = True
                self._attr_is_closing = False
            case OperationalStatus.COVERING_IS_CURRENTLY_CLOSING:
                self._attr_is_opening = False
                self._attr_is_closing = True
            case _:
                self._attr_is_opening = False
                self._attr_is_closing = False

        LOGGER.debug(
            "Movement state for %s: opening=%s closing=%s",
            self.entity_id,
            self._attr_is_opening,
            self._attr_is_closing,
        )

        if self._entity_info.endpoint.has_attribute(
            None, clusters.WindowCovering.Attributes.CurrentPositionLiftPercent100ths
        ):
            # current position is inverted in matter (100 is closed, 0 is open)
            current_cover_position = self.get_matter_attribute_value(
                clusters.WindowCovering.Attributes.CurrentPositionLiftPercent100ths
            )
            self._attr_current_cover_position = (
                100 - floor(current_cover_position / 100)
                if current_cover_position is not None
                else None
            )

            LOGGER.debug(
                "Current position for %s - raw: %s - corrected: %s",
                self.entity_id,
                current_cover_position,
                self.current_cover_position,
            )

        if self._entity_info.endpoint.has_attribute(
            None, clusters.WindowCovering.Attributes.CurrentPositionTiltPercent100ths
        ):
            # current tilt position is inverted in matter (100 is closed, 0 is open)
            current_cover_tilt_position = self.get_matter_attribute_value(
                clusters.WindowCovering.Attributes.CurrentPositionTiltPercent100ths
            )
            self._attr_current_cover_tilt_position = (
                100 - floor(current_cover_tilt_position / 100)
                if current_cover_tilt_position is not None
                else None
            )

            LOGGER.debug(
                "Current tilt position for %s - raw: %s - corrected: %s",
                self.entity_id,
                current_cover_tilt_position,
                self.current_cover_tilt_position,
            )

        # map matter type to HA deviceclass
        device_type: clusters.WindowCovering.Enums.Type = (
            self.get_matter_attribute_value(clusters.WindowCovering.Attributes.Type)
        )
        self._attr_device_class = TYPE_MAP.get(device_type, CoverDeviceClass.AWNING)

        supported_features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        )
        commands = self.get_matter_attribute_value(
            clusters.WindowCovering.Attributes.AcceptedCommandList
        )
        if clusters.WindowCovering.Commands.GoToLiftPercentage.command_id in commands:
            supported_features |= CoverEntityFeature.SET_POSITION
        if clusters.WindowCovering.Commands.GoToTiltPercentage.command_id in commands:
            supported_features |= CoverEntityFeature.SET_TILT_POSITION
        self._attr_supported_features = supported_features


# Discovery schema(s) to map Matter Attributes to HA entities
DISCOVERY_SCHEMAS = [
    MatterDiscoverySchema(
        platform=Platform.COVER,
        entity_description=MatterCoverEntityDescription(
            key="MatterCover",
            name=None,
        ),
        entity_class=MatterCover,
        required_attributes=(
            clusters.WindowCovering.Attributes.OperationalStatus,
            clusters.WindowCovering.Attributes.Type,
        ),
        absent_attributes=(
            clusters.WindowCovering.Attributes.CurrentPositionLiftPercent100ths,
            clusters.WindowCovering.Attributes.CurrentPositionTiltPercent100ths,
        ),
    ),
    MatterDiscoverySchema(
        platform=Platform.COVER,
        entity_description=MatterCoverEntityDescription(
            key="MatterCoverPositionAwareLift", name=None
        ),
        entity_class=MatterCover,
        required_attributes=(
            clusters.WindowCovering.Attributes.OperationalStatus,
            clusters.WindowCovering.Attributes.Type,
            clusters.WindowCovering.Attributes.CurrentPositionLiftPercent100ths,
        ),
        optional_attributes=(
            clusters.WindowCovering.Attributes.TargetPositionLiftPercent100ths,
        ),
        absent_attributes=(
            clusters.WindowCovering.Attributes.CurrentPositionTiltPercent100ths,
        ),
    ),
    MatterDiscoverySchema(
        platform=Platform.COVER,
        entity_description=MatterCoverEntityDescription(
            key="MatterCoverPositionAwareTilt", name=None
        ),
        entity_class=MatterCover,
        required_attributes=(
            clusters.WindowCovering.Attributes.OperationalStatus,
            clusters.WindowCovering.Attributes.Type,
            clusters.WindowCovering.Attributes.CurrentPositionTiltPercent100ths,
        ),
        optional_attributes=(
            clusters.WindowCovering.Attributes.TargetPositionTiltPercent100ths,
        ),
        absent_attributes=(
            clusters.WindowCovering.Attributes.CurrentPositionLiftPercent100ths,
        ),
    ),
    MatterDiscoverySchema(
        platform=Platform.COVER,
        entity_description=MatterCoverEntityDescription(
            key="MatterCoverPositionAwareLiftAndTilt", name=None
        ),
        entity_class=MatterCover,
        required_attributes=(
            clusters.WindowCovering.Attributes.OperationalStatus,
            clusters.WindowCovering.Attributes.Type,
            clusters.WindowCovering.Attributes.CurrentPositionLiftPercent100ths,
            clusters.WindowCovering.Attributes.CurrentPositionTiltPercent100ths,
        ),
        optional_attributes=(
            clusters.WindowCovering.Attributes.TargetPositionLiftPercent100ths,
            clusters.WindowCovering.Attributes.TargetPositionTiltPercent100ths,
        ),
    ),
]
