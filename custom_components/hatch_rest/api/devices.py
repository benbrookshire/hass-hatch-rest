"""Hatch device models built on top of their AWS IoT shadow state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import colorsys
import logging
import math
from typing import TYPE_CHECKING, Any

from .const import (
    MAX_IOT_VALUE,
    MODEL_NAMES,
    REST_MINI_AUDIO_TRACKS,
    REST_MINI_TRACK_NONE,
    REST_PLUS_AUDIO_TRACKS,
    REST_PLUS_TRACK_NONE,
    Product,
)
from .errors import HatchError

if TYPE_CHECKING:
    from .hub import HatchHub

_LOGGER = logging.getLogger(__name__)


def merge_state(previous: dict[str, Any] | None, changes: dict[str, Any] | None) -> dict[str, Any]:
    """Deep-merge shadow `changes` into `previous` (port of assignState)."""
    state = dict(previous or {})
    for key, value in (changes or {}).items():
        if isinstance(value, dict):
            prev = state.get(key)
            state[key] = merge_state(prev if isinstance(prev, dict) else {}, value)
        elif value is not None:
            # null in a shadow document means "cleared", not a real value
            state[key] = value
    return state


def to_percentage(value: float) -> int:
    """IoT value (0-65535) to percent, as the homebridge plugin rounds it."""
    return math.floor(value * 100 / MAX_IOT_VALUE)


def from_percentage(percentage: float) -> int:
    """Percent to IoT value (0-65535)."""
    return math.ceil(percentage / 100 * MAX_IOT_VALUE)


class HatchDevice:
    """A Hatch IoT device whose state lives in an AWS IoT shadow."""

    def __init__(self, info: dict[str, Any], hub: HatchHub) -> None:
        self.info = info
        self._hub = hub
        self.state: dict[str, Any] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._command_lock = asyncio.Lock()

    @property
    def id(self) -> int:
        return self.info["id"]

    @property
    def name(self) -> str:
        return self.info.get("name") or self.model

    @property
    def mac_address(self) -> str:
        return self.info["macAddress"]

    @property
    def thing_name(self) -> str:
        return self.info["thingName"]

    @property
    def product(self) -> str:
        return self.info["product"]

    @property
    def model(self) -> str:
        return MODEL_NAMES.get(self.product, self.product)

    @property
    def hardware_version(self) -> str | None:
        return self.info.get("hardwareVersion") or (self.state or {}).get("deviceInfo", {}).get(
            "hwVersion"
        )

    @property
    def firmware_version(self) -> str | None:
        return (self.state or {}).get("deviceInfo", {}).get("f")

    @property
    def available(self) -> bool:
        return self.state is not None and self.state.get("connected", True) is not False

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback invoked whenever the state changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def handle_shadow(self, full: bool, document_state: dict[str, Any]) -> None:
        """Apply a shadow document (get) or partial update (update/accepted)."""
        reported = document_state.get("reported")
        desired = document_state.get("desired")
        if full:
            self.state = merge_state(reported, desired)
        elif self.state is None:
            # Partial update before we have the full document - wait for get
            return
        else:
            self.state = merge_state(merge_state(self.state, reported), desired)
        for listener in list(self._listeners):
            listener()

    async def async_update(self, desired: dict[str, Any]) -> None:
        """Send a desired-state update. Commands are serialised per device."""
        async with self._command_lock:
            await self._hub.async_shadow_update(self.thing_name, desired)


class RestPlus(HatchDevice):
    """Rest+ (1st gen WiFi): light, sound and battery."""

    audio_tracks = REST_PLUS_AUDIO_TRACKS

    @property
    def _s(self) -> dict[str, Any]:
        return self.state or {}

    @property
    def is_powered(self) -> bool:
        return bool(self._s.get("isPowered"))

    @property
    def volume(self) -> int:
        return to_percentage(self._s.get("a", {}).get("v", 0))

    @property
    def audio_track(self) -> int:
        return self._s.get("a", {}).get("t", REST_PLUS_TRACK_NONE)

    @property
    def color(self) -> dict[str, Any]:
        return self._s.get("c", {})

    @property
    def brightness(self) -> int:
        c = self.color
        if not any(c.get(k) for k in ("r", "g", "b", "R", "W")):
            # when "no" color is selected in the Hatch app, intensity is not
            # zeroed but everything else is
            return 0
        return to_percentage(c.get("i", 0))

    @property
    def is_rainbow(self) -> bool:
        return bool(self.color.get("R"))

    @property
    def hs_color(self) -> tuple[float, float]:
        c = self.color
        if c.get("W"):
            return (0.0, 0.0)
        h, s, _ = colorsys.rgb_to_hsv(
            c.get("r", 0) / MAX_IOT_VALUE,
            c.get("g", 0) / MAX_IOT_VALUE,
            c.get("b", 0) / MAX_IOT_VALUE,
        )
        return (round(h * 360, 1), round(s * 100, 1))

    @property
    def battery_level(self) -> int | None:
        return self._s.get("deviceInfo", {}).get("b")

    async def set_power(self, on: bool) -> None:
        await self.async_update({"isPowered": on})

    async def set_volume(self, percentage: float) -> None:
        await self.async_update({"a": {"v": from_percentage(percentage)}})

    async def set_audio_track(self, track: int, *, power_on: bool = False) -> None:
        update: dict[str, Any] = {"a": {"t": track}}
        if power_on:
            update["isPowered"] = True
        await self.async_update(update)

    async def set_light(
        self,
        *,
        hs: tuple[float, float] | None = None,
        brightness: float | None = None,
        rainbow: bool = False,
        power_on: bool = False,
    ) -> None:
        """Set color (hue 0-360, saturation 0-100) and brightness (0-100)."""
        h, s = hs if hs is not None else self.hs_color
        if brightness is None:
            brightness = self.brightness or 100
        # lights assume 100% brightness in color calculations; intensity is separate
        r, g, b = colorsys.hsv_to_rgb(h / 360, s / 100, 1)
        color: dict[str, Any] = {
            "r": math.floor(r * MAX_IOT_VALUE),
            "g": math.floor(g * MAX_IOT_VALUE),
            "b": math.floor(b * MAX_IOT_VALUE),
            "R": False,
            "W": False,
            "i": from_percentage(brightness),
        }
        if rainbow:
            color.update({"r": 254, "g": 254, "b": 254, "R": True})
        elif hs is None and self.color.get("W"):
            color.update({"r": 255, "g": 255, "b": 255, "W": True})
        update: dict[str, Any] = {"c": color}
        if power_on:
            update["isPowered"] = True
        await self.async_update(update)

    async def light_off(self) -> None:
        await self.async_update({"c": {"i": 0}})


class RestMini(HatchDevice):
    """Rest Mini: sound only."""

    audio_tracks = REST_MINI_AUDIO_TRACKS

    @property
    def _current(self) -> dict[str, Any]:
        return (self.state or {}).get("current", {})

    @property
    def is_playing(self) -> bool:
        return self._current.get("playing", "none") != "none"

    @property
    def volume(self) -> int:
        return to_percentage(self._current.get("sound", {}).get("v", 0))

    @property
    def audio_track(self) -> int:
        return self._current.get("sound", {}).get("id", REST_MINI_TRACK_NONE)

    async def set_volume(self, percentage: float) -> None:
        await self.async_update({"current": {"sound": {"v": from_percentage(percentage)}}})

    async def set_playing(self, playing: bool) -> None:
        if playing:
            await self.async_update({"current": {"playing": "remote", "step": 1}})
        else:
            await self.async_update({"current": {"playing": "none", "step": 0}})

    async def set_audio_track(self, track: int) -> None:
        if track == REST_MINI_TRACK_NONE:
            await self.set_playing(False)
            return
        await self.async_update(
            {
                "current": {
                    "playing": "remote",
                    "step": 1,
                    "sound": {"id": track, "until": "indefinite"},
                }
            }
        )


class RoutineDevice(HatchDevice):
    """Devices driven by routines, exposed as a single on/off switch."""

    @property
    def is_playing(self) -> bool:
        raise NotImplementedError

    async def turn_on_routine(self) -> None:
        raise NotImplementedError

    async def turn_off(self) -> None:
        raise NotImplementedError


class RestIot(RoutineDevice):
    """Rest 2nd Gen, Rest+ 2nd Gen, Restore IoT, Restore 2/3, Hatch Baby."""

    @property
    def is_playing(self) -> bool:
        return (self.state or {}).get("current", {}).get("playing", "none") != "none"

    async def _set_current(self, playing: str, step: int, sr_id: int) -> None:
        await self.async_update(
            {
                "current": {
                    "playing": playing,
                    "step": step,
                    "srId": sr_id,
                    # Must explicitly unpause or the device ignores the command
                    "paused": False,
                }
            }
        )

    async def fetch_routines(self) -> list[dict[str, Any]]:
        """Routines available on the touch ring, in display order."""
        routines = await self._hub.rest.get_routines(self.mac_address)
        routines.sort(key=lambda r: r.get("displayOrder", 0))
        return [
            r
            for r in routines
            # Before an app upgrade only favorites were on the touch ring;
            # after, any routine type can be, flagged with button0
            if r.get("type") == "favorite" or r.get("button0")
        ]

    async def turn_on_routine(self) -> None:
        routines = await self.fetch_routines()
        if not routines:
            raise HatchError(
                f"Unable to turn on {self.name}: no touch ring routines found. "
                "Create a favorite routine in the Hatch app."
            )
        await self._set_current("routine", 1, routines[0]["id"])

    async def turn_off(self) -> None:
        await self._set_current("none", 0, 0)


class Restore(RoutineDevice):
    """Restore (1st gen)."""

    @property
    def is_playing(self) -> bool:
        return (self.state or {}).get("content", {}).get("playing", "none") != "none"

    async def _set_content(self, playing: str, step: int) -> None:
        await self.async_update(
            {"content": {"playing": playing, "paused": False, "offset": 0, "step": step}}
        )

    async def turn_on_routine(self) -> None:
        # first step of the Bedtime routine
        await self._set_content("routine", 1)

    async def turn_off(self) -> None:
        await self._set_content("none", 0)


DEVICE_CLASSES: dict[str, type[HatchDevice]] = {
    Product.REST_PLUS: RestPlus,
    Product.REST_MINI: RestMini,
    Product.RESTORE: Restore,
    Product.RIOT: RestIot,
    Product.RIOT_PLUS: RestIot,
    Product.RESTORE_IOT: RestIot,
    Product.RESTORE_V4: RestIot,
    Product.RESTORE_V5: RestIot,
    Product.REST_BABY: RestIot,
}
