"""Battery sensor for the Hatch Rest+."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HatchConfigEntry
from .api import RestPlus
from .entity import HatchEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HatchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Hatch sensors."""
    async_add_entities(
        HatchBatterySensor(device)
        for device in entry.runtime_data.devices
        if isinstance(device, RestPlus)
    )


class HatchBatterySensor(HatchEntity[RestPlus], SensorEntity):
    """Rest+ battery level."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: RestPlus) -> None:
        super().__init__(device, "battery")

    @property
    def native_value(self) -> int | None:
        return self.device.battery_level
