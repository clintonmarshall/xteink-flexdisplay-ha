# FlexDisplay receiver for Echo Spot (rook)

This Android 11 kiosk client turns the original 2017 Echo Spot (`rook`) into a
managed FlexDisplay target. It uses the same Bridge screen endpoint and device
record as the XTEINK displays while declaring Android, colour, touch, and round
screen capabilities.

## Features

- 480 × 480 circular-safe colour dashboards rendered by Dashboard Studio
- automatic Bridge registration and periodic telemetry
- cached-image and empty-unchanged transfer support
- swipe left/right for previous/next page and tap empty space to refresh
- tap controls for lights, switches, input booleans, and scenes
- hold-and-confirm safety for garage doors and other covers
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
  --es device_id ROOK-LIVINGROOM
```

The Bridge must be reachable directly from the Spot over the trusted LAN. A
Home Assistant ingress URL is not suitable because it requires a browser
session; publish the add-on's TCP port 8099 instead. Confirm with
`http://HOME_ASSISTANT_IP:8099/healthz` before configuring the receiver.

On first launch Android may ask which Home app to use. Choose **FlexDisplay
Spot** and select **Always**. Long-press outside an interactive tile to change
the Bridge address later.

## Touch controls

- Tap a light, switch, input boolean, or scene tile: run its default action.
- Hold a cover tile: show a confirmation before opening or closing it.
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
Spot as `always_on_color`, keeps its sleep plan awake, and ignores battery and
unchanged-image interval multipliers; the 60-second interval remains only as a
recovery fallback.

The LineageOS rook build is experimental and SELinux-permissive. Keep ADB and
the Bridge API on a trusted LAN; do not expose either directly to the internet.
