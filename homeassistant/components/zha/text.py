"""Support for ZHA text entities."""

import functools
from typing import override

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ZHAEntity
from .helpers import (
    SIGNAL_ADD_ENTITIES,
    async_add_entities as zha_async_add_entities,
    convert_zha_error_to_ha_error,
    get_zha_data,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Zigbee Home Automation text entities from config entry."""
    zha_data = get_zha_data(hass)
    entities_to_create = zha_data.platforms[Platform.TEXT]

    unsub = async_dispatcher_connect(
        hass,
        SIGNAL_ADD_ENTITIES,
        functools.partial(
            zha_async_add_entities, async_add_entities, ZhaText, entities_to_create
        ),
    )
    config_entry.async_on_unload(unsub)


class ZhaText(ZHAEntity, TextEntity):
    """Representation of a ZHA text entity."""

    @override
    def _update_capability_attrs(self) -> None:
        """Re-derive capability attributes from the cached state."""
        state = self._zha_state
        self._attr_mode = TextMode(state.mode)
        self._attr_native_min = state.native_min
        self._attr_native_max = state.native_max
        self._attr_pattern = state.pattern

    @property
    @override
    def native_value(self) -> str | None:
        """Return the current value."""
        return self._zha_state.native_value

    @convert_zha_error_to_ha_error()
    @override
    async def async_set_value(self, value: str) -> None:
        """Change the value."""
        await self.entity_data.entity.async_set_value(value=value)
        self.async_write_ha_state()
