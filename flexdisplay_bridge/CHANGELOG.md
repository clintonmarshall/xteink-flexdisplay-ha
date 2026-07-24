# Changelog

## 0.3.1

- Bundled the verified FlexDisplay 0.7.0 OTA release metadata.
- Added a backward-compatible fallback when an existing installation has
  blank or missing firmware options.

## 0.3.0

- Added buffered physical-button event ingestion and recent event history.
- Added USB, SD, uptime, heap, wake-reason, and button summary telemetry.
- Added a per-device events endpoint for integrations and diagnostics.

## 0.2.0

- Added queued refresh, next-screen, restart, and firmware-install commands.
- Added firmware release metadata and battery-gated OTA delivery headers.
- Added online, pending-command, dispatched-command, and result state.

## 0.1.1

- Replaced the compact entity list with a high-contrast two-column card layout.
- Added semantic temperature, humidity, battery, solar, power, and home icons.
- Increased value sizes and added visual battery fill plus Wi-Fi and connection status.
- Expanded the dashboard from seven to eight visible entities.

## 0.1.0

- Initial X3/X4 image renderer.
- Device telemetry registry.
- Home Assistant entity reads.
- MQTT Discovery and queued refresh commands.
