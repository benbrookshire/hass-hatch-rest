"""Switches for Hatch devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HatchConfigEntry
from .api import RestPlus, RoutineDevice
from .entity import HatchEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HatchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Hatch switches."""
    entities: list[SwitchEntity] = []
    for device in entry.runtime_data.devices:
        if isinstance(device, RestPlus):
            entities.append(HatchPowerSwitch(device))
        elif isinstance(device, RoutineDevice):
            entities.append(HatchRoutineSwitch(device))
    async_add_entities(entities)


class HatchPowerSwitch(HatchEntity[RestPlus], SwitchEntity):
    """Master power for light and sound on the Rest+."""

    _attr_translation_key = "power"

    def __init__(self, device: RestPlus) -> None:
        super().__init__(device, "power")

    @property
    def is_on(self) -> bool:
        return self.device.is_powered

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._command(self.device.set_power(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(self.device.set_power(False))


class HatchRoutineSwitch(HatchEntity[RoutineDevice], SwitchEntity):
    """Restore / Rest 2nd Gen family.

    Routines don't map onto simple light/sound controls, so (as in the
    homebridge plugin) turning this on starts the first touch-ring routine
    (Restore: first Bedtime step) and turning it off stops whatever is playing.
    """

    _attr_name = None  # the device's primary feature

    def __init__(self, device: RoutineDevice) -> None:
        super().__init__(device, "routine")

    @property
    def is_on(self) -> bool:
        return self.device.is_playing

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._command(self.device.turn_on_routine())

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(self.device.turn_off())
