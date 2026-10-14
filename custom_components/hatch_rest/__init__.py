"""The Hatch Rest integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.ssl import get_default_context

from .api import HatchAuthError, HatchConnectionError, HatchHub

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
    Platform.SWITCH,
]

type HatchConfigEntry = ConfigEntry[HatchHub]


async def async_setup_entry(hass: HomeAssistant, entry: HatchConfigEntry) -> bool:
    """Set up Hatch Rest from a config entry."""
    hub = HatchHub(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        get_default_context(),
    )
    try:
        await hub.async_start()
    except HatchAuthError as err:
        await hub.async_stop()
        raise ConfigEntryAuthFailed(str(err)) from err
    except HatchConnectionError as err:
        await hub.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HatchConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
