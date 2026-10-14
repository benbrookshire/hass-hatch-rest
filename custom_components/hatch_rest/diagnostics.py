"""Diagnostics for Hatch Rest.

Replaces the homebridge plugin's `"debug": true` option: for products this
integration does not support yet, the device info and shadow state are
included so they can be attached to an issue.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import HatchConfigEntry
from .api import HatchError

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "id",
    "macAddress",
    "thingName",
    "memberId",
    "firstName",
    "lastName",
    "babies",
    "u",  # firmware update url
    "PIP",
    "LIP",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HatchConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    hub = entry.runtime_data
    try:
        unsupported: Any = await hub.async_debug_info()
    except HatchError as err:
        unsupported = {"error": repr(err)}

    return async_redact_data(
        {
            "entry": dict(entry.data),
            "devices": [
                {"product": d.product, "info": d.info, "state": d.state} for d in hub.devices
            ],
            "unsupported_products": hub.unsupported_products,
            "unsupported_devices": unsupported,
        },
        TO_REDACT,
    )
