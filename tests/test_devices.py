"""Device models: state derivation and the shadow updates commands produce."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.hatch_rest.api.devices import (
    RestIot,
    RestMini,
    Restore,
    RestPlus,
    merge_state,
)
from custom_components.hatch_rest.api.errors import HatchError


class FakeHub:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.rest = AsyncMock()

    async def async_shadow_update(self, thing: str, desired: dict[str, Any]) -> None:
        self.updates.append((thing, desired))


DEFAULT_PRODUCTS = {RestPlus: "restPlus", RestMini: "restMini", RestIot: "riot", Restore: "restore"}


def make(cls, state: dict[str, Any] | None = None, product: str | None = None):
    hub = FakeHub()
    device = cls(
        {
            "id": 1,
            "name": "Nursery",
            "macAddress": "AA:BB:CC:DD:EE:FF",
            "thingName": "thing-1",
            "product": product or DEFAULT_PRODUCTS[cls],
        },
        hub,
    )
    if state is not None:
        device.handle_shadow(True, {"reported": state})
    return device, hub


def test_merge_state_is_deep_and_ignores_nulls():
    assert merge_state({"a": {"v": 1, "t": 2}, "x": 1}, {"a": {"v": 5}, "x": None}) == {
        "a": {"v": 5, "t": 2},
        "x": 1,
    }


def test_full_document_desired_overrides_reported():
    device, _ = make(RestPlus, None)
    device.handle_shadow(True, {"reported": {"isPowered": False}, "desired": {"isPowered": True}})
    assert device.is_powered


def test_partial_update_before_full_document_is_ignored():
    device, _ = make(RestPlus, None)
    calls = []
    device.add_listener(lambda: calls.append(1))
    device.handle_shadow(False, {"reported": {"isPowered": True}})
    assert device.state is None
    assert calls == []
    assert not device.available


def test_partial_update_merges_and_notifies():
    device, _ = make(RestPlus, {"isPowered": False, "a": {"v": 0, "t": 2}})
    calls = []
    device.add_listener(lambda: calls.append(1))
    device.handle_shadow(False, {"desired": {"a": {"t": 5}}})
    assert device.audio_track == 5
    assert device.state["a"]["v"] == 0
    assert calls == [1]


def test_unavailable_when_device_reports_disconnected():
    device, _ = make(RestPlus, {"connected": False})
    assert not device.available


def test_rest_plus_state():
    device, _ = make(
        RestPlus,
        {
            "isPowered": True,
            "a": {"v": 32768, "t": 14},
            "c": {"r": 65535, "g": 0, "b": 0, "i": 65535, "R": False, "W": False},
            "deviceInfo": {"b": 87, "f": "1.2.3"},
        },
    )
    assert device.volume == 50
    assert device.audio_track == 14
    assert device.brightness == 100
    assert device.hs_color == (0.0, 100.0)
    assert device.battery_level == 87
    assert device.firmware_version == "1.2.3"


def test_rest_plus_no_color_means_zero_brightness():
    # The Hatch app's "no color" zeroes everything except intensity
    device, _ = make(RestPlus, {"c": {"r": 0, "g": 0, "b": 0, "i": 40000, "R": False, "W": False}})
    assert device.brightness == 0


async def test_rest_plus_set_light():
    device, hub = make(RestPlus, {"isPowered": False, "c": {}})
    await device.set_light(hs=(120, 100), brightness=50, power_on=True)
    assert hub.updates == [
        (
            "thing-1",
            {
                "c": {"r": 0, "g": 65535, "b": 0, "R": False, "W": False, "i": 32768},
                "isPowered": True,
            },
        )
    ]


async def test_rest_plus_rainbow_and_white_are_preserved():
    device, hub = make(RestPlus, {"c": {"r": 255, "g": 255, "b": 255, "W": True, "i": 100}})
    await device.set_light(brightness=20)
    assert hub.updates[-1][1]["c"]["W"] is True
    await device.set_light(rainbow=True)
    assert hub.updates[-1][1]["c"]["R"] is True


async def test_rest_plus_audio_and_power():
    device, hub = make(RestPlus, {"isPowered": True})
    await device.set_volume(100)
    await device.set_audio_track(5, power_on=True)
    await device.set_power(False)
    await device.light_off()
    assert [u for _, u in hub.updates] == [
        {"a": {"v": 65535}},
        {"a": {"t": 5}, "isPowered": True},
        {"isPowered": False},
        {"c": {"i": 0}},
    ]


async def test_rest_mini_commands():
    device, hub = make(
        RestMini, {"current": {"playing": "remote", "sound": {"id": 10128, "v": 65535}}}
    )
    assert device.is_playing
    assert device.audio_track == 10128
    assert device.volume == 100

    await device.set_audio_track(10130)
    await device.set_audio_track(0)
    await device.set_playing(True)
    assert [u for _, u in hub.updates] == [
        {
            "current": {
                "playing": "remote",
                "step": 1,
                "sound": {"id": 10130, "until": "indefinite"},
            }
        },
        {"current": {"playing": "none", "step": 0}},
        {"current": {"playing": "remote", "step": 1}},
    ]


async def test_rest_iot_turns_on_first_touch_ring_routine():
    device, hub = make(RestIot, {"current": {"playing": "none"}})
    hub.rest.get_routines.return_value = [
        {"id": 30, "type": "sleep", "displayOrder": 0, "button0": False},
        {"id": 20, "type": "flex", "displayOrder": 2, "button0": True},
        {"id": 10, "type": "favorite", "displayOrder": 1},
    ]
    await device.turn_on_routine()
    hub.rest.get_routines.assert_awaited_once_with("AA:BB:CC:DD:EE:FF")
    assert hub.updates == [
        ("thing-1", {"current": {"playing": "routine", "step": 1, "srId": 10, "paused": False}})
    ]
    await device.turn_off()
    assert hub.updates[-1][1] == {
        "current": {"playing": "none", "step": 0, "srId": 0, "paused": False}
    }


async def test_rest_iot_without_routines_raises():
    device, hub = make(RestIot, {})
    hub.rest.get_routines.return_value = [{"id": 1, "type": "sleep", "displayOrder": 0}]
    with pytest.raises(HatchError, match="favorite routine"):
        await device.turn_on_routine()
    assert hub.updates == []


async def test_restore_commands():
    device, hub = make(Restore, {"content": {"playing": "routine"}})
    assert device.is_playing
    await device.turn_on_routine()
    await device.turn_off()
    assert [u for _, u in hub.updates] == [
        {"content": {"playing": "routine", "paused": False, "offset": 0, "step": 1}},
        {"content": {"playing": "none", "paused": False, "offset": 0, "step": 0}},
    ]


def test_model_names():
    assert make(RestIot, product="restoreV5")[0].model == "Restore 3"
    assert make(RestIot, product="riotPlus")[0].model == "Rest+ 2nd Gen"
    assert make(RestPlus)[0].model == "Rest+"
