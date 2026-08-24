"""The Open Thread Border Router integration."""

import logging

import aiohttp
import python_otbr_api
import yarl

from homeassistant.components.homeassistant_hardware.helpers import (
    async_notify_firmware_info,
    async_register_firmware_info_provider,
)
from homeassistant.components.thread import async_add_dataset
from homeassistant.config_entries import SOURCE_HASSIO
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from . import homeassistant_hardware, websocket_api
from .const import DOMAIN
from .types import OTBRConfigEntry
from .util import (
    GetBorderAgentIdNotSupported,
    OTBRData,
    async_find_legacy_entries,
    update_issues,
    update_unique_id,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def _async_remove_duplicate_entries(hass: HomeAssistant) -> None:
    """Remove the duplicates an older version of the integration left behind.

    Before HA Core 2026.9, a rediscovery of the add-on could create a second entry
    next to an entry created by the first version of the integration. The older entry
    has no unique id, so it is never matched again while the newer one owns the
    add-on's uuid.
    This can be removed in HA Core 2027.3.
    """
    # A disabled entry is not set up, it must not cause the removal of the entry which
    # is actually used
    claimed_hosts = {
        host
        for entry in hass.config_entries.async_entries(DOMAIN, include_disabled=False)
        if entry.source == SOURCE_HASSIO
        and entry.unique_id is not None
        and (host := yarl.URL(entry.data["url"]).host) is not None
    }
    for host in claimed_hosts:
        for duplicate in async_find_legacy_entries(hass, host):
            _LOGGER.warning(
                "Removing config entry %s, it is a duplicate of the entry for %s",
                duplicate.entry_id,
                host,
            )
            await hass.config_entries.async_remove(duplicate.entry_id)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Open Thread Border Router component."""
    websocket_api.async_setup(hass)

    async_register_firmware_info_provider(hass, DOMAIN, homeassistant_hardware)

    # Runs before the config entries are set up, so a duplicate is never set up
    await _async_remove_duplicate_entries(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: OTBRConfigEntry) -> bool:
    """Set up an Open Thread Border Router config entry."""
    api = python_otbr_api.OTBR(entry.data["url"], async_get_clientsession(hass), 10)

    otbrdata = OTBRData(entry.data["url"], api, entry.entry_id)
    try:
        border_agent_id = await otbrdata.get_border_agent_id()
        dataset_tlvs = await otbrdata.get_active_dataset_tlvs()
        extended_address = await otbrdata.get_extended_address()
    except GetBorderAgentIdNotSupported:
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"get_get_border_agent_id_unsupported_{otbrdata.entry_id}",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="get_get_border_agent_id_unsupported",
        )
        return False
    except (
        HomeAssistantError,
        aiohttp.ClientError,
        TimeoutError,
    ) as err:
        raise ConfigEntryNotReady("Unable to connect") from err
    await update_unique_id(hass, entry, border_agent_id)
    if dataset_tlvs:
        await update_issues(hass, otbrdata, dataset_tlvs)
        await async_add_dataset(
            hass,
            DOMAIN,
            dataset_tlvs.hex(),
            preferred_border_agent_id=border_agent_id.hex(),
            preferred_extended_address=extended_address.hex(),
        )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    entry.runtime_data = otbrdata

    if fw_info := await homeassistant_hardware.async_get_firmware_info(hass, entry):
        await async_notify_firmware_info(hass, DOMAIN, fw_info)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: OTBRConfigEntry) -> bool:
    """Unload a config entry."""
    return True


async def async_reload_entry(hass: HomeAssistant, entry: OTBRConfigEntry) -> None:
    """Handle an options update."""
    await hass.config_entries.async_reload(entry.entry_id)
