# FlexDisplay Roadmap

This roadmap keeps the platform enhancements in a deliberate order. A release
may move only after the previous phase is usable on both XTEINK X3 and X4.

## v0.32 — Edge fleet policy control

Status: implemented and publishing.
Delivery: Bridge/HACS/Firmware/FlexHub `0.32.0`.

- Revisioned fleet policy profiles with durable device acknowledgement.
- Native FlexHub fleet health, policy, scope, default-app, dashboard, and
  Photo Frame controls.
- Matching App-only MQTT and optional custom-integration entities.
- Shared X3/X4 acknowledgement support without splitting the firmware image.

## v0.31 — Refined platform and upstream refresh

Status: released.
Delivery: Bridge/HACS/Firmware/FlexHub `0.31.0`.

- Separate device firmware, Home Assistant, always-on Hub, and Factory Kit
  ownership while retaining one coordinated platform version and manifest.
- CrossPoint 1.5 Reader and FreeInk hardware-stack refresh for both X3 and X4.
- OpenDisplay 2.20-compatible zlib and ordered PIPE transfers with bounded
  recovery for abandoned transfers.
- Current TRMNL/Terminus request and response metadata without permitting stock
  TRMNL firmware to replace FlexDisplay firmware.
- Clean-fleet provisioning and common X3/X4 OTA metadata from one verified
  firmware binary.

## v0.15 — Visual Dashboard Studio

Status: released and deployed.
Delivery: Bridge/HACS `0.11.1`; existing device firmware `0.14.0` is compatible.

- Home Assistant App web editor with live X3 and X4 previews.
- Persisted dashboard profiles that survive App restarts and upgrades.
- One-tile, stacked, side-by-side, and four-tile layouts.
- Home Assistant entity catalogue and per-tile labels, units, and icons.
- Value, gauge, progress, 24-hour history, and QR-code visuals.
- Direct profile assignment with a queued device refresh.

## v0.16 — State-aware pages

Status: released and deployed.
Delivery: Bridge/HACS `0.12.0`; existing device firmware `0.14.0` is compatible.

- Conditional alert pages driven by Home Assistant entity state.
- Priority and expiry rules that restore the normal playlist automatically.
- Scheduled morning, daytime, evening, and overnight page sets.
- Doorbell, alarm, energy, appliance, and weather-alert templates.

## v0.16.1 — Dashboard image tiles

Status: released and deployed.
Delivery: Bridge/HACS `0.13.0`; existing device firmware `0.14.0` is compatible.

- Home Assistant camera and image entity selection in Dashboard Studio.
- Direct HTTP(S) image sources for content outside Home Assistant.
- Crop-to-fill and fit-whole-image controls with exact X3/X4 1-bit previews.
- Bounded downloads and strict separation of Home Assistant credentials.

## v0.17 — Configurable physical-button actions

Status: released and deployed.
Delivery: Bridge/HACS/Firmware `0.17.0`.

- Per-device short-, double-, and long-press mappings.
- Home Assistant service, scene, script, and automation targets.
- Mode-aware actions while preserving page navigation and recovery gestures.

## v0.18 — Photo Frame media pipeline

Status: released and deployed.
Delivery: Bridge/HACS/Firmware `0.18.0`.

- JPEG, PNG, WebP, and BMP upload and conversion.
- E-ink crop, rotation, resize, and dithering previews.
- Albums, shuffle, schedules, captions, and Home Assistant media sources.

## v0.19 — Fleet recovery and OTA observability

Status: released and deployed.
Delivery: Bridge/HACS/Firmware `0.19.0`.

- Cancel queued or already-delivered firmware commands safely.
- Retry failed updates with bounded attempts and configurable backoff.
- Reset a blocked rollout while preserving a bounded audit history.
- Reconcile a USB-recovered device automatically after its next check-in.
- Report preflight, download, validation, flash, reboot, and failure progress.
- Mirror and verify release firmware on the local Bridge before device delivery.

## v0.20 — Device health and screen history

Status: released and deployed.
Delivery: Bridge/HACS `0.20.0`; existing shared firmware `0.19.0` is compatible.

- HACS-optional App-only installation using complete MQTT Discovery.
- Duplicate-safe `hacs`, `mqtt`, and temporary `both` migration modes.
- Fleet Health workspace with battery, SD, Home Assistant, Wi-Fi, power,
  firmware, page, and next-wake status.
- Last rendered screen, bounded recent screen history, and exact one-shot
  screen resend.
- Automatic stale-update release with preserved device and rollout evidence.

Remaining health work is carried forward: reset/crash reasons, Wi-Fi trend
history, battery runtime prediction, and configurable maintenance windows.

## v0.21 — Six-device Fleet Canvas

Status: queued.

- 2 × 3, 1 × 6, and custom X3/X4 arrangements.
- Server-side image tiling and synchronized queued refresh.
- Canvas health, partial-update recovery, and mixed-model calibration.

## v0.22 — Security and release channels

Status: queued.

- Per-device credentials and signed firmware manifests.
- Stable, beta, and development release channels.
- Boot health checks and automatic rollback.

## v0.23 — Transfer optimization

Status: released and deployed.
Delivery: Bridge/Firmware `0.23.0`.

- Zero-byte unchanged-screen responses only when the device confirms that the
  matching image is physically cached.
- PNG Photo Frame transport with automatic BMP conversion on X3/X4.
- Backwards-compatible full-image delivery for firmware before `0.23.0`.
- Home Assistant diagnostics for transfer format, delivered bytes, bytes
  saved, and percentage saved.

## v0.24 — Advanced fleet health

Status: implemented, pending release and canary deployment.
Delivery: Bridge/Firmware `0.24.0`.

- Device boot identifiers and reset reasons for power, software, panic,
  watchdog, deep-sleep, brownout, external, and unknown resets.
- Bounded check-in, reset, battery, Wi-Fi, SD, memory, and uptime history.
- Battery drain/runtime estimates, Wi-Fi trend, missed-check-in detection,
  repeated SD failure counters, and problem-reset diagnostics.
- Fleet Health battery and Wi-Fi sparklines plus complete MQTT Discovery
  diagnostic and problem entities.
- Optional timezone-aware overnight OTA maintenance windows with a
  configurable USB-powered override.
- Automatic repair of stale OTA acknowledgement evidence.

## v0.33 — Fleet Management and FlexHub control

Status: implemented, pending release deployment.
Delivery: Bridge App and FlexHub `0.33.0`; X3/X4 firmware `0.32.0` remains compatible.

- Dedicated Fleet Management workspace alongside Dashboard Studio.
- Reusable custom power and wake policy builder, plus built-in Battery Saver,
  Balanced, and USB Kiosk profiles.
- Policy, scope, default application, dashboard, album, and delivery controls.
- Selected-device policy targeting with desired/reported revision and
  acknowledgement status for every X3/X4.
- Confirmed, canary-first OTA plans for all devices, X3, X4, or a selected set.
- Automatic bounded continuation after successful canary and device reboot
  acknowledgement, with progress, blocker, retry, cancellation, and reset UI.
- Persistent FlexHub connection and a Studio health workspace for network,
  storage, receiver fleet, and Meshtastic/MQTT status.
- Home Assistant MQTT discovery for the SenseCAP FlexHub.
- Scrollable 24-device managed-display list on the SenseCAP touchscreen.
- FlexHub status schema 4 with Wi-Fi, memory, uptime, Meshtastic node, and
  Meshtastic MQTT diagnostics.

## v0.34 — Mixed Content and on-device Quick Menu

Status: implemented and locally validated, pending canary deployment.
Delivery: Bridge App and shared X3/X4 firmware `0.34.0`; existing dashboards,
Reader, OpenDisplay, Photo Frame, TRMNL, fleet policy, and OTA paths are retained.

- Reusable Mixed Content channels combine the dashboard profile already
  assigned to each device with Message, Quote, and RSS/Atom pages.
- Message screens support large text, normal/important/critical emphasis,
  footers, and optional QR links.
- Quote screens support built-in offline quotes or a custom collection, with a
  stable quote for each day or explicit random selection.
- News screens support digest, headline, and summary layouts, bounded downloads,
  caching, stale-on-error delivery, attribution, and Australian source presets.
- Dashboard Studio can create, reorder, preview, assign, unassign, and refresh
  content channels without requiring YAML or SD-card files.
- Holding Confirm in Home Assistant mode opens the device Quick Menu; short
  Confirm refreshes, directional buttons retain page navigation, and long
  Confirm is reserved in the Studio action mapper.
- The device and Bridge negotiate mixed-content support and exchange page title,
  type, position, and selection metadata for later fleet diagnostics.
