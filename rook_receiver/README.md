# FlexDisplay Android receiver

This Android 11 kiosk client turns Amazon LineageOS devices into managed
FlexDisplay targets. It currently supports the original 2017 Echo Spot (`rook`)
and the 2019 Echo Show 5 (`checkers`). It uses the same Bridge screen endpoint
and device record as the XTEINK displays while declaring Android, colour, touch,
and device-specific screen capabilities.

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
- immediate screen invalidation over the notification long poll, with a
  one-minute safety poll if the push connection is interrupted
- long press for local Bridge URL and device ID settings
- Home/launcher and boot-completed integration
- remote refresh, page navigation, clear, sleep, restart, and power-off-style
  blanking through the existing Home Assistant entities
- explicit rejection of ESP32 firmware installation commands

## Build

The project uses Android Gradle Plugin 7.4.2 and Gradle 7.6.4. Set
`sdk.dir` in an untracked `local.properties` to an Android SDK containing API
33, then run:

```bash
./gradlew clean assembleDebug lintDebug
```

The debug APK is written to
`app/build/outputs/apk/debug/app-debug.apk`.

## Install and configure

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n au.com.ldcs.flexdisplay.rook/.MainActivity \
  --es bridge_url http://HOME_ASSISTANT_IP:8099 \
  --es device_id CHECKERS-SHOW501
```

The Bridge must be reachable directly from the receiver over the trusted LAN. A
Home Assistant ingress URL is not suitable because it requires a browser
session; publish the add-on's TCP port 8099 instead. Confirm with
`http://HOME_ASSISTANT_IP:8099/healthz` before configuring the receiver.

On first launch Android may ask which Home app to use. Choose **FlexDisplay**
and select **Always**. Long-press outside an interactive tile to change
the Bridge address later.

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
Android receiver as `always_on_color`, keeps its sleep plan awake, and ignores
battery and unchanged-image interval multipliers; the 60-second interval remains
only as a recovery fallback.

## Home Assistant Assist

Version 0.3.0 of the Android receiver adds a local push-to-talk Assist button.
It sends 16 kHz mono PCM audio to the Bridge `/assist` endpoint using the
receiver token and plays the returned PCM response through the Echo device
speaker. Android will ask for microphone permission on first use; it can also be
granted over ADB:

```bash
adb shell pm grant au.com.ldcs.flexdisplay.rook android.permission.RECORD_AUDIO
```

This uses Home Assistant's configured Assist pipeline. It does not expose the
Echo camera as a Home Assistant camera entity; current LineageOS builds report
camera hardware features but no public camera devices through Android's camera
service.

Version 0.4.0 adds Android fleet controls. The Bridge and Home Assistant
integration can set receiver speaker volume, mute/unmute, set app brightness,
restart the receiver app, and trigger a test chime.

The LineageOS builds are experimental and SELinux-permissive. Keep ADB and the
Bridge API on a trusted LAN; do not expose either directly to the internet.
