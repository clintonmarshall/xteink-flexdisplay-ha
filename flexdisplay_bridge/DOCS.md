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
- zero to four Home Assistant, synthetic `device.*`, or fixed-content tiles;
- automatic or explicit e-ink-safe icons;
- large value, gauge, progress, 24-hour history, QR, name-card, or image visual
  treatment;
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

For content that does not come from Home Assistant, select **Fixed content (no
HA entity)**. Fixed values are stored in the dashboard profile and can be used
for labels, notices, room cards, instructions, QR content, and ID passes.
Select **QR code** and enter a URL or any text in **Text or URL to encode**.
The **Name card / ID pass** starter template provides editable full-name,
role/title, organisation/ID, and QR fields. Fixed-only pages render and preview
without a Home Assistant token.

Saving a profile queues a refresh for every device currently assigned to it.
Sleeping devices receive that refresh on their next scheduled or physical
button wake.

## Photo Frame media library

Open **Photo Frame** in Dashboard Studio to create and manage server-side
albums. The library accepts JPEG, PNG, WebP, and BMP files up to 8 MB and 20
megapixels. It also captures the current image from a Home Assistant
`camera.*` or `image.*` entity. Source files stay in the App data volume and
survive upgrades.

Each image supports a caption, 90-degree rotation steps, and either crop-to-fill
or fit-whole-image rendering. The X3/X4 preview uses the same resize,
autocontrast, caption, and Floyd–Steinberg dithering path used for the device
BMP.

Albums support ordered or deterministic shuffled playback, a rotation interval,
timezone, and an active window, including overnight windows. Outside the active
window the current e-paper image remains visible and the Bridge schedules the
device's next wake for the beginning of the next window.

Assigning an album changes that device to `photo_frame` mode and queues a
refresh. Short Right/Down presses request the next image; Left/Up request the
previous image. The Home Assistant integration exposes the current album,
filename, and position after the device checks in.

## Physical-button actions

Firmware, Bridge, and integration `0.17.0` classify short, double, and long
presses in Home Assistant mode. Open **Physical-button actions** in Dashboard
Studio, choose a fleet device, button, gesture, and action, then save it.

Available actions are:

- next, previous, overview, or refresh dashboard navigation;
- toggle, turn on, or turn off a Home Assistant entity;
- activate a `scene.*`, `script.*`, or `automation.*` entity;
- call a selected `domain.service` with optional JSON service data;
- do nothing, or restore the compatibility-preserving default.

Short Right/Down presses default to the next page and short Left/Up presses
default to the previous page. Confirm has no default remote action. Back and
Power are deliberately reserved for escape, wake, and recovery and cannot be
remapped.

Mappings apply only while the device is in Home Assistant mode. Each gesture is
tagged with its originating mode before being buffered on SD, so a press made
in Reader, TRMNL, OpenDisplay, or Photo Frame cannot execute later when the
device checks into the Bridge. Replayed HTTP requests are de-duplicated by the
event sequence, button, and device uptime before any service call.

Studio's activation card reports **Ready** when the selected device currently
uses Home Assistant mode and **Waiting** otherwise. Saving is immediate and
does not require a firmware-configuration sync; the action runs after the
device reports the gesture to the Bridge. Enable **Show assigned-button
indicators** to add a narrow screen legend above the device status footer:
dotted means short press, double-dotted means double press, and solid means
long press. The indicator is included at the device's next screen render.

Options:

- `dashboard_title`: heading rendered on each e-paper screen.
- `mqtt_enabled`: publish optional MQTT Discovery entities.
- `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password`: dedicated broker
  connection.
- `home_assistant_entity_source`: use `hacs` for the existing custom
  integration, `mqtt` for the v0.20 App-only experience, or `both` only during
  a short migration test. The default is `hacs`, which actively removes
  retained FlexDisplay MQTT Discovery configurations to prevent duplicate
  entities.
- `screen_history_enabled`: retain recent e-paper output in the App data
  volume.
- `screen_history_limit`: number of rendered screens retained per device,
  between 1 and 20.
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
- `firmware_retry_limit`: maximum automatic/manual retries after a failed
  install.
- `firmware_retry_backoff_seconds`: minimum delay before another retry.
- `firmware_mirror_enabled`: download and verify the configured firmware once,
  then serve the trusted copy from the local Bridge.
- `firmware_mirror_retry_seconds`: delay before retrying a failed mirror
  download.
- `firmware_stale_install_seconds`: release and audit an install that has
  stopped reporting progress for this many seconds.

## App-only Home Assistant installation

FlexDisplay v0.20 can create its complete Home Assistant device through MQTT,
so HACS is optional. First install and configure the Mosquitto broker and Home
Assistant MQTT integration. In the FlexDisplay Bridge App configuration:

1. Enable MQTT and enter the broker connection.
2. Change **Home Assistant entity source** from `hacs` to `mqtt`.
3. Save and restart the Bridge App.
4. Wake each X3/X4 once so its complete device and retained state are
   published.

Existing HACS users should leave the source set to `hacs` until they are ready
to migrate. Remove the existing FlexDisplay integration entry, change the App
source to `mqtt`, restart the Bridge, and wake the displays. Home Assistant
will create MQTT-backed devices; dashboard cards that referenced old entity
IDs may need to be pointed at the replacement entities. Do not leave `both`
enabled permanently because it intentionally creates parallel entity sets.

App-only discovery includes fleet health, page navigation, refresh, sleep,
restart, provisioning switches and numbers, profile/mode selectors, physical
button events, a current-screen Image entity, and a native firmware update
entity with live percentage progress. Commands remain durable in the Bridge
while a display sleeps.

## Always-on colour displays and push refresh

The Bridge treats the Echo Spot receiver as an always-on colour display. It
uses the receiver's authenticated long poll to invalidate the current screen
as soon as a command is durably queued. The built-in **Always-on Colour** fleet
policy is also available for mains-powered LCD/OLED panels. This runtime class
stays awake, uses LAN-preferred delivery, disables battery and unchanged-image
interval scaling, and keeps a 60-second safety poll.

An ESP display can opt into the same runtime class by reporting these values in
`X-FlexDisplay-Capabilities`:

```text
color,lcd,always-on-color,mqtt-screen-refresh
```

`oled` may replace `lcd`; `mains-powered` is accepted in place of
`always-on-color`. With MQTT connected, every queued device command publishes
a non-retained JSON wake event to:

```text
flexdisplay/<device_id>/event/screen
```

The device should subscribe to that topic and immediately GET
`/api/v1/screen` when `event` is `screen_refresh`. The payload includes
`reason`, `command_id`, and `queued_at`. MQTT is a best-effort wake transport:
the command remains durable in the Bridge, and the safety poll still delivers
it after broker or Wi-Fi recovery. Battery e-paper devices do not opt in and
retain their existing scheduled-sleep behavior.

## Fleet health and screen history

Open **Fleet health** in Dashboard Studio to see every display's current
health, battery, Wi-Fi signal, power state, firmware, page, and next wake.
Problems are summarized as SD-card, Home Assistant, low-battery, connectivity,
or firmware-update issues.

The Bridge retains the configured number of distinct rendered screens for
each device. Selecting a thumbnail queues that exact image for a one-shot
resend on the device's next check-in; normal dashboard or Photo Frame content
resumes afterwards. History is stored under the App data volume and is bounded
per device.

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
the timer. **Cancel active commands** removes both queued commands and durable
commands already delivered to a device. Firmware `0.19.0` polls the Bridge
during an update and honours cancellation before validation or flashing.

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
update role, update status, stage, percentage, exact failure, failure time, and
command-ID diagnostics in its attributes. Separate sensors expose the stage,
percentage, error, and error timestamp.

Firmware and Bridge `0.19.0` add bounded retry and recovery controls. **Retry
firmware** becomes available after the configured backoff while the retry limit
has not been reached. **Reset firmware rollout** cancels outstanding install
commands and starts a fresh canary gate for the same release while retaining
the previous rollout in bounded audit history.

With `firmware_mirror_enabled`, the Bridge downloads the configured application
image, verifies its exact size and SHA-256, caches it atomically, and gives
devices a local Bridge URL. A failed mirror never replaces a previously
verified image. Its status is available from the Bridge health endpoint, and
the authenticated mirror-refresh API forces another verification attempt.

If an old canary firmware consumes an install but cannot acknowledge it, first
make and verify a complete flash backup, install the exact configured target
over USB, and confirm the device checks in with USB power and a ready SD card.
The **Verify USB firmware recovery** button becomes available only while all
those conditions pass. A `0.19.0` Bridge also automatically reconciles a device
that checks in over USB with the exact target firmware, even if its earlier
durable command was lost. Manual verification records separate USB-recovery
evidence in the Bridge audit history; neither route impersonates a device
acknowledgement.

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
