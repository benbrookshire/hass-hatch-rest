"""Sound machine controls for the Hatch Rest+ and Rest Mini."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HatchConfigEntry
from .api import RestMini, RestPlus
from .api.const import REST_MINI_TRACK_NONE, REST_PLUS_TRACK_NONE
from .entity import HatchEntity

SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.SELECT_SOURCE
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HatchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Hatch sound machines."""
    async_add_entities(
        HatchSoundMachine(device)
        for device in entry.runtime_data.devices
        if isinstance(device, (RestPlus, RestMini))
    )


class HatchSoundMachine(HatchEntity[RestPlus | RestMini], MediaPlayerEntity):
    """Audio track (as source) and volume."""

    _attr_translation_key = "sound"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = SUPPORTED_FEATURES

    def __init__(self, device: RestPlus | RestMini) -> None:
        super().__init__(device, "sound")
        self._none_track = (
            REST_PLUS_TRACK_NONE if isinstance(device, RestPlus) else REST_MINI_TRACK_NONE
        )
        self._tracks = {
            track: name for track, name in device.audio_tracks.items() if track != self._none_track
        }
        self._attr_source_list = list(self._tracks.values())
        self._last_track = next(iter(self._tracks))

    @property
    def _playing(self) -> bool:
        device = self.device
        if isinstance(device, RestPlus):
            return device.is_powered and device.audio_track != REST_PLUS_TRACK_NONE
        return device.is_playing

    @property
    def state(self) -> MediaPlayerState:
        return MediaPlayerState.PLAYING if self._playing else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float:
        return self.device.volume / 100

    @property
    def source(self) -> str | None:
        return self._tracks.get(self.device.audio_track)

    def _handle_device_update(self) -> None:
        # remembered so turn_on can resume the last sound on a Rest+
        if self.device.audio_track in self._tracks:
            self._last_track = self.device.audio_track
        super()._handle_device_update()

    async def async_set_volume_level(self, volume: float) -> None:
        await self._command(self.device.set_volume(volume * 100))

    async def async_select_source(self, source: str) -> None:
        track = next((t for t, name in self._tracks.items() if name == source), None)
        if track is None:
            raise ServiceValidationError(f"Unknown sound: {source}")
        await self._play(track)

    async def async_turn_on(self) -> None:
        if isinstance(self.device, RestMini):
            await self._command(self.device.set_playing(True))
        else:
            await self._play(self._last_track)

    async def async_turn_off(self) -> None:
        if isinstance(self.device, RestMini):
            await self._command(self.device.set_playing(False))
        else:
            await self._command(self.device.set_audio_track(REST_PLUS_TRACK_NONE))

    async def _play(self, track: int) -> None:
        self._last_track = track
        device = self.device
        if isinstance(device, RestPlus):
            await self._command(device.set_audio_track(track, power_on=not device.is_powered))
        else:
            await self._command(device.set_audio_track(track))
