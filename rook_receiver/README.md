# FlexDisplay Android receiver

This Android receiver turns Amazon LineageOS devices and intermittently used
Android phones into managed FlexDisplay targets. It supports two separately
installable flavors:

- `kiosk` preserves the original Echo Spot (`rook`) and Echo Show 5 (`checkers`)
  launcher, boot, lock-screen, and always-on behavior.
- `companion` is a normal phone app with a rectangular display profile. It is
  visible in Recents and suspends display polling, notification long polling,
  and camera work whenever it is not in the foreground.

Both flavors use the same Bridge screen protocol and device records as the
XTEINK displays while declaring their Android, colour, touch, audio, and
device-specific capabilities.

## Features

- 480 × 480 circular-safe Spot dashboards rendered by Dashboard Studio
- 960 × 480 landscape Show 5 dashboards rendered by Dashboard Studio
- automatic Bridge registration and periodic telemetry
- cached-image and empty-unchanged transfer support
- swipe left/right for previous/next page and tap empty space to refresh
- tap controls for lights, switches, input booleans, and scenes
- hold-and-confirm safety for garage doors and other covers
- press-and-hold Assist voice control through the FlexDisplay Bridge and Home
  Assistant Assist pipeline, with local speaker playback
- immediate camera notifications, local chimes, dismissal, and action buttons
- explicit camera, microphone, audio, touch, always-on, screen-resolution, and
  device-class telemetry for Home Assistant capability entities
- immediate screen invalidation over the notification long poll, with a
  one-minute safety poll if the push connection is interrupted
- long press for local Bridge URL and device ID settings
- Home/launcher and boot-completed integration
- remote refresh, page navigation, clear, sleep, restart, and power-off-style
  blanking through the existing Home Assistant entities
- explicit rejection of ESP32 firmware installation commands
- companion-phone speaker volume, mute, and test-chime controls
- local push-to-talk microphone policy exposed to Home Assistant
- explicit one-shot companion camera snapshots, with no live stream or
  background capture

## Build

The project uses Android Gradle Plugin 7.4.2 and Gradle 7.6.4. Set
`sdk.dir` in an untracked `local.properties` to an Android SDK containing API
33, then run:

```bash
./gradlew clean assembleKioskDebug assembleCompanionDebug \
  lintKioskDebug lintCompanionDebug
```

The debug APKs are written to:

- `app/build/outputs/apk/kiosk/debug/app-kiosk-debug.apk`
- `app/build/outputs/apk/companion/debug/app-companion-debug.apk`

## Install and configure

Install the Echo kiosk flavor with:

```bash
adb install -r app/build/outputs/apk/kiosk/debug/app-kiosk-debug.apk
adb shell am start -n au.com.ldcs.flexdisplay.rook/.MainActivity
```

Install the phone companion flavor with:

```bash
adb install -r app/build/outputs/apk/companion/debug/app-companion-debug.apk
adb shell am start \
  -n au.com.ldcs.flexdisplay.rook.companion/au.com.ldcs.flexdisplay.rook.MainActivity
```

The Bridge must be reachable directly from the receiver over the trusted LAN. A
Home Assistant ingress URL is not suitable because it requires a browser
session; publish the add-on's TCP port 8099 instead. Confirm with
`http://HOME_ASSISTANT_IP:8099/healthz` before configuring the receiver.

On the Echo kiosk flavor, Android may ask which Home app to use. Choose
**FlexDisplay** and select **Always**. The companion flavor never registers as a
Home app. Long-press outside an interactive tile to change the Bridge address
later in either flavor. For security, launch intents cannot provision or change
the Bridge URL or device ID. Changing either connection value creates a new
receiver token, so delete the old Bridge record before reusing its device ID;
saving unchanged values preserves the existing pairing.

## Touch controls

- Tap a light, switch, input boolean, or scene tile: run its default action.
- Hold a cover tile: show a confirmation before opening or closing it.
- Hold **Assist**: record while held, send the request to Home Assistant Assist,
  and play the response on the device speaker.
- Tap empty space: refresh the current page.
- Swipe left: next page.
- Swipe right: previous page.
- Long press outside an interactive tile: connection settings.

## Home Assistant notifications

Version 0.43.0 adds the `flexdisplay.notify` action. A doorbell automation can
show a camera snapshot, play a two-tone chime, and offer bounded controls:

```yaml
action: flexdisplay.notify
data:
  device_id: ROOK-SPOT01
  title: Front door
  message: Someone rang the doorbell
  camera_entity: camera.front_door
  chime: doorbell
  duration: 20
  actions:
    - label: Porch light
      service: light.turn_on
      entity_id: light.porch
    - label: Open garage
      service: cover.open_cover
      entity_id: cover.garage_door
```

Opening a cover always requires confirmation, even if an automation omits the
confirmation flag. Notification actions are limited to lights, switches,
input booleans, scenes, and cover open/close/stop services. The Spot uses a
Google-free long-poll connection to receive notifications immediately.

The same connection carries `screen_refresh` events. Saving and pushing a
Studio page, changing a profile, or issuing a device command wakes the receiver
immediately instead of waiting for its periodic screen request. If a refresh
arrives while an image is already downloading, the receiver remembers it and
fetches once more after the active request completes. The Bridge classifies the
Echo kiosk receiver as `always_on_color`, keeps its sleep plan awake, and
ignores battery and unchanged-image interval multipliers. The companion does
not advertise `always-on-color`; it pauses both polling loops while backgrounded.

## Home Assistant Assist

Version 0.3.0 of the Android receiver adds a local push-to-talk Assist button.
It sends 16 kHz mono PCM audio to the Bridge `/assist` endpoint using the
receiver token and plays the returned PCM response through the Echo device
speaker. Android asks for microphone permission only after the local user holds
**Assist**; Bridge state can never open the microphone permission prompt.

This uses Home Assistant's configured Assist pipeline. FlexDisplay does not yet
expose the Echo camera as a Home Assistant camera entity. Notification snapshots
come from a Home Assistant `camera.*` or `image.*` entity configured in the
automation. If the installed LineageOS build or camera shim exposes camera
hardware to Android, receiver `0.5.0` reports that capability to the Bridge so
Home Assistant can show capability-aware entities and dashboards.

Version 0.4.0 adds Android fleet controls. The Bridge and Home Assistant
integration can set receiver speaker volume, mute/unmute, set app brightness,
restart the receiver app, and trigger a test chime.

Version 0.5.0 adds hardware capability telemetry. The receiver reports camera,
microphone, audio, touch, always-on colour display class, device class, and
screen resolution through explicit Bridge headers. Older receivers still work;
the Bridge falls back to the original comma-separated capabilities where it can.

## Phone camera, microphone, and speaker entities

An Android companion reports camera, microphone, speaker, battery, and USB
state to the Bridge. The Home Assistant integration creates these controls only
for a trusted companion capability record:

- **Take snapshot** queues one foreground camera capture. The phone prefers the
  front camera, falls back to the rear camera, removes source metadata by
  decoding and re-encoding the JPEG, and uploads only for the matching one-time
  command ID.
- **Camera snapshot** returns only the most recently requested image. The
  Bridge keeps that JPEG in memory for five minutes and does not offer a live
  stream. Viewing the camera entity never triggers a new capture.
- **Allow push-to-talk microphone** permits or disables the on-device Assist
  button. A new phone starts disabled until this switch is explicitly enabled;
  it cannot start a remote recording, and disabling it stops an active local
  recording.
- **Speaker** supports volume set, volume step, and mute. **Test chime** remains
  a separate button; arbitrary media playback is not advertised.

Android offers camera permission through a visible local explanation when the
companion opens and requests microphone permission only when Assist is first
held. A snapshot captures only while the app is foregrounded and the phone is
unlocked, displays visible capture/upload status, times out after 15 seconds,
and uploads a re-encoded JPEG capped at 2 MiB. Keep the Bridge and receiver on a
trusted LAN and use HTTPS or a trusted private network when traffic crosses an
untrusted segment.

The LineageOS builds are experimental and SELinux-permissive. Keep ADB and the
Bridge API on a trusted LAN; do not expose either directly to the internet.
