"""Tianjin Water: direct, read-only account data."""

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import WaterClient
from .const import CONF_CUSTOM_ID, CONF_SESSION, CONF_USER_AGENT, DEFAULT_USER_AGENT, DOMAIN
from .coordinator import WaterCoordinator

type WaterConfigEntry = ConfigEntry[WaterCoordinator]

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: WaterConfigEntry) -> bool:
    """Set up from the UI, with independent cookies for each account."""
    coordinator = WaterCoordinator(
        hass,
        entry,
        WaterClient(
            async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar()),
            entry.data[CONF_CUSTOM_ID],
            entry.data[CONF_SESSION],
            entry.data.get(CONF_USER_AGENT, DEFAULT_USER_AGENT),
        ),
    )
    # Check the persisted stop state before making any network request.
    coordinator._raise_if_stopped()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    ir.async_delete_issue(hass, DOMAIN, entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WaterConfigEntry) -> bool:
    """Unload entities; the coordinator cancels its timers on entry unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: WaterConfigEntry) -> None:
    """Remove any remaining repair notice."""
    ir.async_delete_issue(hass, DOMAIN, entry.entry_id)
