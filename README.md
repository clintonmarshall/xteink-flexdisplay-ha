# XTEINK FlexDisplay for Home Assistant

Public Home Assistant installer for FlexDisplay-enabled XTEINK X3 and X4,
the Zectrix Note 4 House Pulse e-paper surface, and the original 2017 Echo
Spot (`rook`) running the FlexDisplay Android receiver.

This repository provides:

- the **FlexDisplay Bridge** Home Assistant App, which renders device-sized
  dashboard images, records fleet telemetry, and securely brokers Note 4
  push-to-talk audio through the Home Assistant Assist pipeline. Voice Remote
  v2 shows what Home Assistant heard and answered, preserves short follow-up
  conversations, and returns speaker-ready local audio;
- the **FlexDisplay** HACS integration, which creates Home Assistant devices,
  sensors, queued controls, connectivity state, firmware-update entities, and
  physical-button events.
- **Dashboard Studio**, a visual profile editor with live X3/X4 previews,
  readable e-ink layouts, state-aware alerts and schedules, and direct fleet
  assignment.
- the **Echo Spot receiver**, a 480 × 480 circular colour kiosk with touch
  navigation, Bridge telemetry, Home-launcher startup, and safe exclusion from
  embedded-device firmware rollouts.

## Source and release authority

The project's internal Forgejo repository is the source of truth for
development, pull requests, tags, and releases. GitHub is an automatic,
read-only compatibility mirror used by HACS and public release downloads.
Contributors must open pull requests and publish tags in Forgejo only. See
`docs/ARCHITECTURE.md` and `docs/RELEASE.md` for the durable workflow.

This is the Home Assistant component of the wider FlexDisplay Platform:

- `xteink-flexdisplay` owns the common X3/X4 firmware;
- `xteink-flexdisplay-ha` owns the Bridge App, Studio, and integration;
- `xteink-flexhub` owns always-on ESP32/SenseCAP fleet relay firmware;
- `xteink-flexdisplay-release` owns Factory Kit packaging and coordinated
  release manifests.

Release binaries may be attached here for Home Assistant delivery, but their
source remains in `xteink-flexdisplay`. Factory backups, credentials, device
identities, and private network configuration are never committed here.

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
Home Assistant entity conditions. Six starter templates cover doorbells,
alarms, daytime energy, running appliances, weather alerts, and ID passes.

Tiles can also use fixed content without a Home Assistant entity. The
standalone Name Card / ID Pass template combines large identity fields with a
QR code, while the QR editor converts a typed URL or arbitrary text directly
into an e-ink-safe code. Fixed-only profiles remain usable when Home Assistant
entity access is unavailable.

The App uses Home Assistant's internal API token. No long-lived Home Assistant
token is required in the App options.

### Fleet Policies and FlexHub

Studio `0.33.0` adds two workspaces in its left sidebar:

- **Fleet Policies** assigns a power/wake profile, default application,
  dashboard, or Photo Frame album to every display, an X3/X4 model group, or
  selected devices. Each row shows the desired and reported policy revisions
  so sleeping-device acknowledgements are explicit.
- **FlexHub** stores the SenseCAP hub address and optional access PIN, then
  shows its network, SD storage, receiver fleet, Meshtastic node, and
  Meshtastic MQTT status. With MQTT Discovery enabled, the hub also appears as
  a Home Assistant device.

The same Fleet Policy controls remain available in the FlexHub browser page.
On its touchscreen, open **Policies** and swipe vertically inside **Managed
Displays** to inspect larger fleets.

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

Bridge and integration `0.20.0` add an optional HACS-free installation path.
With MQTT enabled and **Home Assistant entity source** set to `mqtt`, the
Bridge creates the complete device, diagnostics, controls, configuration
entities, button events, and firmware update entity through Home Assistant
MQTT Discovery. Existing installations default to `hacs` so retained MQTT
Discovery records are removed instead of creating duplicates.

Dashboard Studio now includes Fleet Health and a bounded history of the exact
screens rendered for each X3/X4. Operators can review battery, connectivity,
SD-card, Home Assistant, firmware, power, page, and next-wake state, then
resend a saved image on the next device check-in. Shared firmware `0.19.0`
remains compatible with this Bridge release.

Firmware, Bridge, and integration `0.21.0` add Branded Fetch Screens. Use
Dashboard Studio to upload a company logo or icon, set a headline and message,
include the device name, owner, or area, select an X3/X4 layout, and preview
the exact e-paper result. Devices verify and cache the one-bit BMP on their SD
card only when its SHA-256 changes, then render it locally while fetching a
dashboard. Fleet-default and per-device designs support always, manual-wake,
USB-only, and disabled policies, with the original text screen retained as a
safe fallback.

Firmware, Bridge, and integration `0.22.0` add an LDCS Factory Kit and
acknowledged fleet content packs. The private local USB kit can return either
an X3 or X4 to a known firmware and SD-card setup. For working devices,
Dashboard Studio's Fleet Content workspace distributes revised managed photos,
books, logos, and assets to one or many displays without erasing personal
files or device settings.

Firmware and Bridge `0.23.0` add capability-negotiated screen transfer
optimization. A device with a verified cached frame receives a zero-byte
unchanged response, while Photo Frame mode uses PNG instead of raw BMP.
Older firmware retains the full-image protocol, and Home Assistant receives
per-device transfer-size and savings diagnostics.

Firmware and Bridge `0.24.0` add advanced fleet health. X3/X4 devices report
their boot identity and reset reason, while the Bridge keeps bounded check-in,
reset, battery, Wi-Fi, SD, memory, and uptime history. Fleet Health shows
trend sparklines, battery-runtime estimates, missed check-ins, reset/watchdog
diagnostics, SD failure counts, and optional timezone-aware OTA maintenance
windows. MQTT Discovery publishes the same diagnostics and problem sensors.

Bridge App and FlexHub `0.33.0` separate screen design from fleet operations.
The new Fleet Management workspace creates reusable power and wake profiles,
deploys a policy, default application, dashboard, or Photo Frame album to all
devices, one model, or a selected set, and tracks each acknowledgement. It also
starts a confirmed, canary-first OTA plan from Dashboard Studio, automatically
advances after successful reboot acknowledgement, observes per-device progress,
and exposes retry, cancellation, and rollout reset. FlexHub status, storage,
network, receiver, and Meshtastic/MQTT diagnostics are visible in the same Home
Assistant App. Existing X3/X4 firmware `0.32.0` remains compatible.

Bridge App and shared X3/X4 firmware `0.34.0` add Mixed Content channels and an
on-device Quick Menu. In **Dashboard Studio → Mixed content**, build a playlist
from the device's assigned Home Assistant dashboards, large Message screens,
daily or random Quote pages, and RSS/Atom News pages. Preview and assign the
channel there; no YAML or SD-card file is required. While a Home Assistant page
is showing, hold **Confirm** to open the Quick Menu, or press Confirm briefly to
refresh the current page.

Bridge App, Home Assistant integration, and FlexHub platform `0.35.0` add the
**Meshtastic Console**. Open **Dashboard Studio → FlexHub** to read live direct
and channel messages, filter the bounded history, inspect node and radio signal
details, send a broadcast or direct message, request a direct-message
acknowledgement, and save quick replies. Incoming-message rules can match a
prefix such as `ALERT:` and queue a large message screen to selected X3/X4
displays. The console also exposes receiver scan, delivery, retry, and cancel
controls without leaving Home Assistant.

The HACS integration exposes the `flexdisplay.send_meshtastic_message` action,
the `flexdisplay_meshtastic_message` event, a native Meshtastic event entity,
and last-message, sender, channel, time, and unread-count sensors. For example:

```yaml
action: flexdisplay.send_meshtastic_message
data:
  text: "Showroom closes at 17:00"
  destination: broadcast
  channel: 0
  request_ack: false
```

```yaml
trigger:
  - platform: event
    event_type: flexdisplay_meshtastic_message
    event_data:
      type: message_received
action:
  - service: persistent_notification.create
    data:
      title: Meshtastic message
      message: "{{ trigger.event.data.text }}"
```

App-only installations receive equivalent MQTT Discovery sensors, an event
entity, a broadcast text control, and an unread-reset button. The compact MQTT
text entity accepts up to 220 printable ASCII characters; Studio, the native
Home Assistant action, and direct JSON MQTT commands retain the full 220-byte
UTF-8 validation. Direct delivery acknowledgement means the mesh reported a
routing result, while broadcast delivery can only be reported as queued/sent
because broadcasts have no single recipient acknowledgement.

## Security

Keep port 8099 on a trusted LAN. Configure a Bridge API key before exposing
command endpoints to other networks, and set a FlexHub PIN before using its
message or fleet APIs on a shared network. LoRa fleet commands are disabled by
default and, when enabled, accept only locally favourited Meshtastic nodes. The
hub retains at most 32 recent sender names and message bodies in plain text on
its SD card (or internal fallback storage), so treat that media as sensitive.
Never commit Home Assistant tokens, MQTT passwords, Wi-Fi credentials, device
SD-card contents, or flash backups.

## License

GPL-3.0. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
