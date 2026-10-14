# Hatch Rest for Home Assistant

A Home Assistant integration for Hatch WiFi sound machines: the Rest+, Rest Mini, Rest 2nd Gen, Rest+ 2nd Gen, Restore, Restore 2, Restore 3 and Hatch Baby.

This is a port of [homebridge-hatch-baby-rest](https://github.com/dgreif/homebridge-hatch-baby-rest). It talks to the same Hatch cloud API and receives state over the same AWS IoT push connection, so changes made in the Hatch app or on the device show up in Home Assistant right away. It doesn't poll.

## Entities

| Device | Entities |
| --- | --- |
| **Rest+** | `light` night light (color, brightness, rainbow effect) · `media_player` sound (track as source, volume) · `switch` power · `sensor` battery |
| **Rest Mini** | `media_player` sound (track as source, volume) |
| **Rest 2nd Gen, Rest+ 2nd Gen, Restore IoT, Restore 2, Restore 3, Hatch Baby** | `switch`: on starts the first routine on the touch ring, off stops whatever is playing |
| **Restore** (1st gen) | `switch`: on starts the first step of your Bedtime routine, off stops it |

The routine-based devices run multi-step routines that don't map well onto light and sound controls, so like the homebridge plugin they're exposed as a single switch. If turning one on fails with "no touch ring routines found", create a favorite routine in the Hatch app.

Firmware versions appear on each device page.

### Differences from the homebridge plugin

Home Assistant has richer entity types than HomeKit, so a few things work differently:

- **Sound is a `media_player`, not a fan.** Tracks are listed by name in the source list, instead of being encoded as fan speeds.
- **Turning on the Rest+ light or sound also powers on the device.** The separate Power switch is still there as the master switch.
- **There's no `debug` option.** Download diagnostics instead (see below).
- **Bluetooth Rest devices aren't supported.** The original 1st-gen Rest is Bluetooth-only, and this integration only talks to the Hatch cloud.

## Installation

### HACS (recommended)

1. In HACS, open the menu, choose **Custom repositories**, and add `https://github.com/benbrookshire/hass-hatch-rest` with category **Integration**.
2. Install **Hatch Rest**, then restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Hatch Rest**.
4. Sign in with the email and password you use in the Hatch Sleep app.

### Manual

Copy `custom_components/hatch_rest` into your Home Assistant `config/custom_components/` directory, restart, and add the integration as in step 3 above.

## Unsupported devices

If you own a Hatch product this integration doesn't recognize, the log will show `Unsupported Hatch product found: <name>`. To help add support:

1. Go to **Settings → Devices & services → Hatch Rest**, open the ⋮ menu, and choose **Download diagnostics**.
2. Open an issue and attach the file.

The diagnostics file includes each device's info and live state, including any unsupported devices. Your email, password, names, MAC addresses and device IDs are redacted.

## How it works

1. Logs in to `prod-sleep.hatchbaby.com` and lists your IoT devices.
2. Exchanges a Hatch-issued Cognito token for temporary AWS credentials.
3. Opens one SigV4-signed MQTT-over-websocket connection to AWS IoT and subscribes to each device's [device shadow](https://docs.aws.amazon.com/iot/latest/developerguide/iot-device-shadows.html).
4. Commands are published as `desired` shadow updates, one at a time per device.

The connection lifecycle is ported directly from the homebridge plugin, which went through several rounds of hardening:

- Credentials are rotated every 50 minutes.
- If the connection stays down for 10 minutes, the client is replaced with fresh credentials. paho's built-in reconnect reuses the original signed URL, and that URL stops working once its credentials expire.
- The replacement client is always created before the old one is ended. If creation fails, it's retried forever with exponential backoff.
- A command sent during a reconnect waits for the replacement client instead of being dropped.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

`custom_components/hatch_rest/api/` has no Home Assistant dependencies, and you can test it on its own.

## Credits

The API research, device mappings and connection hardening come from [homebridge-hatch-baby-rest](https://github.com/dgreif/homebridge-hatch-baby-rest) by Dusty Greif. MIT licensed.
