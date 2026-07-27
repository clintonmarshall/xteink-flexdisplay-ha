# FlexDisplay Roadmap

This roadmap keeps the platform enhancements in a deliberate order. A release
may move only after the previous phase is usable on both XTEINK X3 and X4.

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

Status: implemented, pending release and Home Assistant deployment.
Delivery: Bridge/HACS/Firmware `0.19.0`.

- Cancel queued or already-delivered firmware commands safely.
- Retry failed updates with bounded attempts and configurable backoff.
- Reset a blocked rollout while preserving a bounded audit history.
- Reconcile a USB-recovered device automatically after its next check-in.
- Report preflight, download, validation, flash, reboot, and failure progress.
- Mirror and verify release firmware on the local Bridge before device delivery.

## v0.20 — Device health and screen history

Status: queued.

- Reset/crash diagnostics, SD-card health, Wi-Fi history, and battery prediction.
- Last rendered screen, recent screen history, and fleet freshness overview.
- Configurable maintenance windows and stale-device alerts.

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
