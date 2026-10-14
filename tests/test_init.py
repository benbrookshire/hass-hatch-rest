"""Setup, entities and services, with the Hatch cloud faked at the hub boundary."""

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_HS_COLOR
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_EMAIL, CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hatch_rest.api import HatchHub
from custom_components.hatch_rest.api.devices import DEVICE_CLASSES
from custom_components.hatch_rest.api.errors import HatchAuthError, HatchConnectionError
from custom_components.hatch_rest.const import DOMAIN

DEVICES = [
    ("restPlus", "Nursery", "AA:00:00:00:00:01"),
    ("restMini", "Mini", "AA:00:00:00:00:02"),
    ("restoreV4", "Bedroom", "AA:00:00:00:00:03"),
]
STATES: dict[str, dict[str, Any]] = {
    "restPlus": {
        "isPowered": True,
        "a": {"v": 32768, "t": 5},
        "c": {"r": 65535, "g": 0, "b": 0, "i": 65535, "R": False, "W": False},
        "deviceInfo": {"b": 80, "f": "4.1.0"},
    },
    "restMini": {"current": {"playing": "none", "sound": {"id": 10128, "v": 0}}},
    "restoreV4": {"current": {"playing": "none"}, "deviceInfo": {"f": "9.0"}},
}


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data={CONF_EMAIL: "parent@example.com", CONF_PASSWORD: "pw"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def updates():
    """Fake the cloud: devices are discovered and shadow updates are recorded."""
    sent: list[tuple[str, dict[str, Any]]] = []

    async def start(self: HatchHub) -> None:
        for i, (product, name, mac) in enumerate(DEVICES):
            info = {
                "id": i,
                "name": name,
                "macAddress": mac,
                "thingName": f"thing-{product}",
                "product": product,
            }
            device = DEVICE_CLASSES[product](info, self)
            self.devices.append(device)
            self._by_thing[device.thing_name] = device

    async def shadow_update(self: HatchHub, thing: str, desired: dict[str, Any]) -> None:
        sent.append((thing, desired))

    with (
        patch.object(HatchHub, "async_start", start),
        patch.object(HatchHub, "async_shadow_update", shadow_update),
        patch.object(HatchHub, "async_stop", AsyncMock()),
    ):
        yield sent


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> HatchHub:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def push_states(hass: HomeAssistant, hub: HatchHub) -> None:
    for device in hub.devices:
        device.handle_shadow(True, {"reported": STATES[device.product]})


async def test_entities_unavailable_until_shadow_arrives(hass, entry, updates):
    hub = await setup(hass, entry)
    assert hass.states.get("light.nursery_night_light").state == STATE_UNAVAILABLE

    push_states(hass, hub)
    await hass.async_block_till_done()

    light = hass.states.get("light.nursery_night_light")
    assert light.state == "on"
    assert light.attributes[ATTR_BRIGHTNESS] == 255
    assert light.attributes[ATTR_HS_COLOR] == (0.0, 100.0)
    assert hass.states.get("switch.nursery_power").state == "on"
    assert hass.states.get("sensor.nursery_battery").state == "80"

    sound = hass.states.get("media_player.nursery_sound")
    assert sound.state == "playing"
    assert sound.attributes["source"] == "Ocean"
    assert sound.attributes["volume_level"] == 0.5

    assert hass.states.get("media_player.mini_sound").state == "off"
    assert hass.states.get("switch.bedroom").state == "off"


async def test_firmware_version_lands_in_device_registry(hass, entry, updates):
    from homeassistant.helpers import device_registry as dr

    hub = await setup(hass, entry)
    push_states(hass, hub)
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "aa:00:00:00:00:01")})
    assert device.sw_version == "4.1.0"
    assert device.model == "Rest+"


async def test_services_send_shadow_updates(hass, entry, updates):
    hub = await setup(hass, entry)
    push_states(hass, hub)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "light",
        "turn_on",
        {
            ATTR_ENTITY_ID: "light.nursery_night_light",
            ATTR_HS_COLOR: (240, 100),
            ATTR_BRIGHTNESS: 128,
        },
        blocking=True,
    )
    assert updates[-1] == (
        "thing-restPlus",
        {"c": {"r": 0, "g": 0, "b": 65535, "R": False, "W": False, "i": 32768}},
    )

    await hass.services.async_call(
        "media_player",
        "select_source",
        {ATTR_ENTITY_ID: "media_player.mini_sound", "source": "Rain"},
        blocking=True,
    )
    assert updates[-1][1]["current"]["sound"]["id"] == 10130

    await hass.services.async_call(
        "media_player",
        "turn_off",
        {ATTR_ENTITY_ID: "media_player.nursery_sound"},
        blocking=True,
    )
    assert updates[-1][1] == {"a": {"t": 0}}

    await hass.services.async_call(
        "media_player",
        "volume_set",
        {ATTR_ENTITY_ID: "media_player.nursery_sound", "volume_level": 0.25},
        blocking=True,
    )
    assert updates[-1][1] == {"a": {"v": 16384}}

    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: "switch.nursery_power"}, blocking=True
    )
    assert updates[-1][1] == {"isPowered": False}


async def test_routine_switch_errors_surface_to_caller(hass, entry, updates):
    hub = await setup(hass, entry)
    push_states(hass, hub)
    await hass.async_block_till_done()

    with (
        patch.object(hub.rest, "get_routines", AsyncMock(return_value=[])),
        pytest.raises(HomeAssistantError, match="favorite routine"),
    ):
        await hass.services.async_call(
            "switch", "turn_on", {ATTR_ENTITY_ID: "switch.bedroom"}, blocking=True
        )


async def test_unload(hass, entry, updates):
    hub = await setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hub.async_stop.assert_awaited()
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (HatchAuthError("bad"), ConfigEntryState.SETUP_ERROR),
        (HatchConnectionError("down"), ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_failures(hass, entry, error, state):
    with (
        patch.object(HatchHub, "async_start", AsyncMock(side_effect=error)),
        patch.object(HatchHub, "async_stop", AsyncMock()),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is state
    if isinstance(error, HatchAuthError):
        assert any(entry.async_get_active_flows(hass, {"reauth"}))
