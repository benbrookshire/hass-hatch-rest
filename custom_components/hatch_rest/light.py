"""Night light for the Hatch Rest+."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.color import brightness_to_value, value_to_brightness

from . import HatchConfigEntry
from .api import RestPlus
from .entity import HatchEntity

EFFECT_RAINBOW = "rainbow"
PERCENT_SCALE = (1, 100)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HatchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Hatch lights."""
    async_add_entities(
        HatchLight(device) for device in entry.runtime_data.devices if isinstance(device, RestPlus)
    )


class HatchLight(HatchEntity[RestPlus], LightEntity):
    """The Rest+ night light. Turning it on also powers the device on."""

    _attr_translation_key = "night_light"
    _attr_color_mode = ColorMode.HS
    _attr_supported_color_modes = {ColorMode.HS}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = [EFFECT_RAINBOW]

    def __init__(self, device: RestPlus) -> None:
        super().__init__(device, "light")

    @property
    def is_on(self) -> bool:
        return self.device.is_powered and self.device.brightness > 0

    @property
    def brightness(self) -> int | None:
        brightness = self.device.brightness
        return value_to_brightness(PERCENT_SCALE, brightness) if brightness else None

    @property
    def hs_color(self) -> tuple[float, float]:
        return self.device.hs_color

    @property
    def effect(self) -> str | None:
        return EFFECT_RAINBOW if self.device.is_rainbow else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = None
        if ATTR_BRIGHTNESS in kwargs:
            brightness = max(1, round(brightness_to_value(PERCENT_SCALE, kwargs[ATTR_BRIGHTNESS])))
        await self._command(
            self.device.set_light(
                hs=kwargs.get(ATTR_HS_COLOR),
                brightness=brightness,
                rainbow=kwargs.get(ATTR_EFFECT) == EFFECT_RAINBOW
                or (ATTR_HS_COLOR not in kwargs and self.device.is_rainbow),
                power_on=not self.device.is_powered,
            )
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(self.device.light_off())
