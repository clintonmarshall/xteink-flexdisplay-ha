# Configuration

The default configuration starts without MQTT and renders a bridge-status
screen until entity IDs are added to `config.yaml`.

## Dashboard Studio

Open **FlexDisplay Studio** from the Home Assistant sidebar or use the App's
**Open Web UI** button. The editor loads Home Assistant's entity catalogue,
renders a live 1-bit preview for either XTEINK X3 or X4, and can assign a saved
profile directly to a fleet device.

Profiles created in Studio are written to
`/data/flexdisplay-dashboards.json`. This file belongs to the App data volume
and is preserved across upgrades and restarts. Once that file exists, it is the
authoritative visual-profile set; `config.yaml` remains available for initial
seeding and advanced non-dashboard configuration.

Each page supports:

- automatic, single, stacked, side-by-side, or four-tile layout;
- zero to four Home Assistant or synthetic `device.*` tiles;
- automatic or explicit e-ink-safe icons;
- large value, gauge, progress, 24-hour history, QR, or image visual treatment;
- optional automatic page rotation.
- normal-playlist, scheduled-page-set, or priority-alert activation.

For an image tile, select a Home Assistant `camera.*` or `image.*` entity, or
choose **Image URL** and enter an HTTP(S) address reachable from the App. Image
tiles can crop to fill their card or contain the whole source. The live preview
uses the same 1-bit Floyd–Steinberg conversion sent to X3/X4 devices.

Direct image downloads are limited to 8 MB and 20 megapixels, reject embedded
URL credentials and non-image responses, and never receive the Home Assistant
bearer token. Home Assistant entity pictures are fetched with authorization
only from the configured Home Assistant origin.

Scheduled pages replace the normal playlist during their configured local time
window; overnight ranges are supported. Alert pages can compare an entity
using equality, numeric thresholds, text containment, on/off semantics, or
availability. Active alerts are ordered by priority ahead of the current
scheduled or normal page set. Optional expiry is measured from Home
Assistant's `last_changed` timestamp and rearms after the entity changes.

Saving a profile queues a refresh for every device currently assigned to it.
Sleeping devices receive that refresh on their next scheduled or physical
button wake.

Options:

- `dashboard_title`: heading rendered on each e-paper screen.
- `mqtt_enabled`: publish optional MQTT Discovery entities.
- `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password`: dedicated broker
  connection.
- `bridge_api_key`: protects bridge command endpoints and should also be
  entered in the HACS integration.
- `firmware_version`: latest FlexDisplay application version offered to devices.
- `firmware_url`: direct HTTP(S) URL for the application `firmware.bin`.
- `firmware_sha256`: lowercase SHA-256 digest for that exact file.
- `firmware_size`: exact firmware file size in bytes.
- `firmware_minimum_battery`: minimum charge percentage for an unplugged update.
- `firmware_canary_required`: require a verified canary before fleet installs.
- `firmware_require_usb_for_canary`: require external power for the canary.
- `firmware_max_parallel`: maximum simultaneous queued/dispatched installs.

## Zero-touch provisioning

FlexDisplay 0.10.0 fleet builds register on their first bridge request. Defaults
for unknown devices are controlled by:

```yaml
provisioning:
  enabled: true
  default_area: ""
  default_mode: home_assistant
  auto_start: true
  refresh_interval_seconds: 900
```

An explicit entry under `devices` can set `name`, `area`, `profile`, `mode`,
`auto_start`, and `refresh_interval_seconds`. The initial assignment is
persisted in the bridge state store. Home Assistant exposes the assigned
dashboard profile and mode as select entities.

## Intelligent sleep

Firmware 0.11.0 asks the Bridge for a sleep plan after each Home Assistant
screen request. The provisioning defaults above control that plan:

- `active_start`, `active_end`, and `timezone` define the daily update window.
- `critical_battery_percent` fully powers the device off until a manual wake.
- `low_battery_percent` and `low_battery_multiplier` reduce update frequency.
- `unchanged_image_multiplier` sleeps longer when the rendered image hash is
  unchanged, and the firmware skips the e-paper refresh.
- `stay_awake_on_usb` keeps controls responsive while external power is present.
- `manual_wake_grace_seconds` leaves a manually woken device interactive before
  returning it to scheduled sleep.

The same keys can be overridden for an individual device under `devices`.
Home Assistant reports the selected action, reason, duration, next wake time,
and whether the last image was unchanged. A scheduled sleep intentionally
differs from the reader's normal power-off action: it retains the X3/X4 power
latch so the ESP32-C3 timer can wake the firmware.

## Remote control

Firmware 0.12.0 and integration 0.9.0 expose queued controls for refresh,
forced redraw, page navigation, clearing the e-paper panel, restart, timed
sleep, and power-off. Configuration entities change live polling, auto-start,
sleep policy, active hours, refresh cadence, battery policy, display identity,
area, and timezone without editing an SD-card file.

Commands sent while a device is sleeping remain queued until its timer or a
physical button wakes it. **Power off until button wake** intentionally disables
the timer. **Cancel pending commands** removes commands not yet delivered.

## Safer firmware rollout

Firmware 0.13.0 and integration 0.10.0 assign durable IDs to commands and keep
results on the device SD card until the Bridge confirms the matching ID. Stale
results cannot clear a newer command.

For each newly configured release, the first eligible update becomes the
canary. With the default options it must report a ready SD card and USB power.
The fleet remains blocked until that device reboots into the exact target
firmware and acknowledges completion. Further installs run one at a time by
default, and any failure pauses the rollout until a new release is configured.

The Firmware entity shows install blockers, rollout state, canary identity,
update role, update status, and command-ID diagnostics in its attributes.

If an old canary firmware consumes an install but cannot acknowledge it, first
make and verify a complete flash backup, install the exact configured target
over USB, and confirm the device checks in with USB power and a ready SD card.
The **Verify USB firmware recovery** button becomes available only while all
those conditions pass and the matching stuck install command is still active.
Pressing it records separate USB-recovery evidence in the Bridge audit history;
it does not impersonate a device acknowledgement.

Some X4 revisions stop reporting USB power after charging completes even while
their USB serial connection remains active. The recovery API can additionally
accept a recent `macos_ioreg` observation containing a USB serial that matches
the device-ID suffix, `/dev/cu.usbmodem*` port, and the SHA-256 of the verified
full-flash backup. This evidence is retained in both device and rollout audit
history.

## Dashboard profiles

Without a configured profile, the Bridge generates eight readable pages from
each device's entities. For explicit control, add named profiles under
`dashboard.profiles` in `config.yaml`, set `dashboard.default_profile`, and
assign a profile to each device:

```yaml
dashboard:
  default_profile: home
  profiles:
    home:
      auto_rotate_seconds: 0
      pages:
        - title: Overview
          entities:
            - sensor.outdoor_temperature
            - sensor.home_battery
        - title: Device Health
          entities:
            - device.battery
            - device.uptime
            - device.storage
            - device.memory

devices:
  X4-123456:
    profile: home
```

Each page uses at most four large tiles. Home Assistant exposes Previous
screen, Next screen, Return to overview, and a direct Dashboard page selector.
Set `auto_rotate_seconds` to zero to disable rotation.

The device downloads to SD, verifies the configured size and SHA-256 digest,
then uses the existing dual-partition firmware installer. Firmware `0.6.0`
must first be installed over USB before queued OTA commands are understood.

Bridge `0.3.0` accepts the SD-buffered physical-button events and extended
telemetry sent by firmware `0.7.0`. The HACS integration exposes these through
the Physical Button event entity and diagnostic sensors.

The app exposes TCP port `8099` on the Home Assistant host.
