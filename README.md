# XTEINK FlexDisplay for Home Assistant

Public Home Assistant installer for FlexDisplay-enabled XTEINK X3 and X4
e-paper devices.

This repository provides:

- the **FlexDisplay Bridge** Home Assistant App, which renders device-sized
  dashboard images and records fleet telemetry;
- the **FlexDisplay** HACS integration, which creates Home Assistant devices,
  sensors, queued controls, connectivity state, firmware-update entities, and
  physical-button events.
- **Dashboard Studio**, a visual profile editor with live X3/X4 previews,
  readable e-ink layouts, state-aware alerts and schedules, and direct fleet
  assignment.

It intentionally contains no device firmware, factory backups, credentials,
device identities, or private network configuration.

## 1. Install the Bridge App

1. In Home Assistant, open **Settings → Apps → App store**.
2. Open the repository menu and add:

   ```text
   https://github.com/clintonmarshall/xteink-flexdisplay-ha
   ```

3. Install **FlexDisplay Bridge**.
4. Start it and enable automatic startup.
5. Open `http://HOME_ASSISTANT_IP:8099/healthz` and confirm that it reports
   `"status": "ok"`.

Open **FlexDisplay Studio** from the Home Assistant sidebar, or select
**Open Web UI** on the App. Existing YAML dashboard profiles are imported on
first start. Studio saves later edits to the App data directory, where they
survive App upgrades and restarts.

Dashboard Studio supports automatic, single-spotlight, stacked, side-by-side,
and four-tile layouts. Tiles can display a large value and semantic icon, a
gauge, progress bar, 24-hour history sparkline, QR code, or a dithered image
from a Home Assistant camera/image entity or direct HTTP(S) URL. Use its X3/X4
selector to inspect the exact device-sized render before assigning the profile.
Pages can also be scheduled by time or promoted to priority alerts using live
Home Assistant entity conditions. Five starter templates cover doorbells,
alarms, daytime energy, running appliances, and weather alerts.

The App uses Home Assistant's internal API token. No long-lived Home Assistant
token is required in the App options.

## 2. Install the HACS integration

1. Open **HACS → Integrations**.
2. Open **Custom repositories**.
3. Add:

   ```text
   https://github.com/clintonmarshall/xteink-flexdisplay-ha
   ```

   Select category **Integration**.

4. Install **FlexDisplay** and restart Home Assistant.
5. Open **Settings → Devices & services → Add integration → FlexDisplay**.
6. Enter `http://HOME_ASSISTANT_IP:8099`.
7. If the Bridge App has an API key, enter the same key.

## 3. Provision each X3/X4

FlexDisplay 0.10.0 fleet builds connect to their approved Wi-Fi and register
with the Bridge automatically. The first response assigns the device name,
area, dashboard profile, mode, refresh interval, and auto-start policy. No
per-device SD-card file is required. Hold **Back** during startup to bypass
auto-start and open the CrossPoint reader.

For standard builds or advanced overrides, place this file on the device SD
card at:

```text
/.crosspoint/home-assistant.json
```

```json
{
  "snapshot_url": "http://HOME_ASSISTANT_IP:8099/api/v1/screen",
  "auth_token_obf": "",
  "refresh_interval_seconds": 300,
  "live_mode": true
}
```

Use `live_mode: true` while testing or while USB-powered. Use `false` for a
battery-safe one-shot update. A sleeping ESP32 cannot receive an immediate
network command; queued commands run at the next device check-in.

## Entities

For each device known to the bridge, the integration exposes:

- battery level;
- Wi-Fi signal;
- last check-in;
- firmware version;
- active FlexDisplay mode;
- awake, sleeping, or offline state;
- pending command and last command result;
- online connectivity;
- refresh, forced-redraw, previous-screen, next-screen, overview, clear,
  timed-sleep, power-off, restart, and pending-command cancellation buttons;
- direct dashboard-page selection;
- live-control, auto-start, intelligent-sleep, and USB stay-awake switches;
- refresh/sleep timing, active-hours, battery-policy, name, area, and timezone
  controls;
- firmware update availability and installation.
- USB and SD-card state, battery voltage, uptime, memory, and wake reason;
- last physical button, press count, and a native Physical Button event entity.

Firmware `0.6.0` or newer is required for queued controls and OTA updates.
Install `0.6.0` once over USB; subsequent application updates can be staged
through Home Assistant when the device checks in. Keep the device on USB power
for the first OTA test.

Firmware `0.7.0`, Bridge `0.3.0`, and integration `0.3.0` are required for
physical-button events and extended telemetry. Events are delivered within
seconds in live/USB-powered mode or buffered until the next check-in while the
device sleeps.

Firmware `0.8.0`, Bridge `0.6.0`, and integration `0.6.0` add configurable
per-device dashboard profiles, page selection, previous/overview navigation,
and optional automatic page rotation.

Firmware `0.10.0`, Bridge `0.7.0`, and integration `0.7.0` add zero-touch
registration, persistent device assignments, dashboard-profile selection, and
assigned-mode selection.

Firmware `0.11.0`, Bridge `0.8.0`, and integration `0.8.0` add intelligent
scheduled sleep, active hours, battery-aware refresh throttling, unchanged-image
suppression, and automatic entity creation for newly discovered fleet devices.

Firmware `0.12.0`, Bridge `0.9.0`, and integration `0.9.0` add complete queued
remote control and editable per-device fleet policy. Sleeping devices receive
commands at their next timer or physical-button wake; a radio wake is not
possible while the ESP32-C3 is in deep sleep.

Firmware `0.13.0`, Bridge `0.10.0`, and integration `0.10.0` add safer
canary-first firmware rollout. Commands carry durable IDs, results remain on
the SD card until explicitly acknowledged, and fleet installation is blocked
until a USB-powered canary boots and verifies the target release.

Bridge and integration `0.12.0` add state-aware dashboard pages, priority and
expiry rules, timezone-aware scheduled page sets, Dashboard Studio alert
templates, and an active-page-set sensor. Existing firmware `0.14.0` is
compatible and does not need to be reflashed.

Bridge and integration `0.13.0` add Dashboard Studio image tiles from Home
Assistant entities and direct URLs, crop/contain controls, and bounded,
credential-isolated image retrieval. Existing firmware `0.14.0` remains
compatible and does not need to be reflashed.

Firmware, Bridge, and integration `0.17.0` add configurable short, double, and
long physical-button actions in Home Assistant mode. Use Dashboard Studio to
map Confirm or a direction button to dashboard navigation, entity controls,
scenes, scripts, automations, or another Home Assistant service. Short arrows
retain their existing page-navigation defaults; Back and Power remain reserved
for escape, wake, and recovery.

Firmware, Bridge, and integration `0.18.0` add a persistent Photo Frame media
library to Dashboard Studio. Upload JPEG, PNG, WebP, or BMP images, preview the
exact dithered X3/X4 result, organize albums, add captions and rotations, import
Home Assistant camera/image entities, configure active hours and shuffle, then
assign an album to any fleet device. Devices fetch one server-rendered BMP,
retain it while asleep, and wake at the album interval or next active window.

Firmware, Bridge, and integration `0.19.0` make fleet updates recoverable and
observable. Home Assistant can cancel queued or delivered updates, retry a
failed update with bounded backoff, reset a blocked rollout, and verify a USB
recovery. Devices report preflight, download, validation, flash, reboot, and
failure progress with exact timestamps. The Bridge downloads the configured
release once, verifies its size and SHA-256, then serves the trusted local copy
to X3 and X4 devices while preserving canary-first rollout gating.

## Security

Keep port 8099 on a trusted LAN. Configure a Bridge API key before exposing
command endpoints to other networks. Never commit Home Assistant tokens, MQTT
passwords, Wi-Fi credentials, device SD-card contents, or flash backups.

## License

GPL-3.0. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
