# FlexDisplay receiver for Echo Spot (rook)

This Android 11 kiosk client turns the original 2017 Echo Spot (`rook`) into a
managed FlexDisplay target. It uses the same Bridge screen endpoint and device
record as the XTEINK displays while declaring Android, colour, touch, and round
screen capabilities.

## Features

- 480 × 480 circular-safe colour dashboards rendered by Dashboard Studio
- automatic Bridge registration and periodic telemetry
- cached-image and empty-unchanged transfer support
- swipe left/right for previous/next page and tap to refresh
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
Spot** and select **Always**. Long-press anywhere on the dashboard to change the
Bridge address later.

## Touch controls

- Tap: refresh the current page.
- Swipe left: next page.
- Swipe right: previous page.
- Long press: connection settings.

The LineageOS rook build is experimental and SELinux-permissive. Keep ADB and
the Bridge API on a trusted LAN; do not expose either directly to the internet.
