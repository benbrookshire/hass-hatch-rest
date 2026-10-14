"""Base entity for Hatch devices."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.entity import Entity

from .api import HatchDevice, HatchError
from .const import DOMAIN, MANUFACTURER


class HatchEntity[D: HatchDevice](Entity):
    """An entity backed by a Hatch device's shadow state (push updates)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: D, key: str) -> None:
        self.device = device
        mac = format_mac(device.mac_address)
        self._attr_unique_id = f"{mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac)},
            manufacturer=MANUFACTURER,
            model=device.model,
            name=device.name,
            hw_version=device.hardware_version,
        )

    @property
    def available(self) -> bool:
        return self.device.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.device.add_listener(self._handle_device_update))

    def _handle_device_update(self) -> None:
        # firmware arrives with the shadow state, after the device is registered
        firmware = self.device.firmware_version
        if firmware and self.device_entry and self.device_entry.sw_version != firmware:
            dr.async_get(self.hass).async_update_device(self.device_entry.id, sw_version=firmware)
        self.async_write_ha_state()

    async def _command(self, command: Awaitable[None]) -> None:
        """Run a device command, surfacing failures to the caller."""
        try:
            await command
        except HatchError as err:
            raise HomeAssistantError(str(err)) from err
