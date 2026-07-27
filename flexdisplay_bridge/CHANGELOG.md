# Changelog

## 0.17.0

- Added per-device short-, double-, and long-press mappings for Confirm, Left,
  Right, Up, and Down in Home Assistant mode.
- Added Dashboard Studio controls for default/no-op behavior, page navigation,
  entity toggle/on/off, scene/script/automation activation, and validated
  Home Assistant service calls.
- Preserved short-arrow page navigation by default and kept Back and Power
  reserved for escape, wake, and recovery.
- Added gesture mode tagging so button presses from Reader, TRMNL, OpenDisplay,
  or Photo Frame cannot be replayed later as Home Assistant actions.
- Added replay-safe action execution results to the device API, native event
  attributes, and Home Assistant sensors.
- Bundled the runtime-detected X3/X4 FlexDisplay `0.17.0` firmware manifest.

## 0.13.0

- Added first-class Dashboard Studio image tiles sourced from Home Assistant
  `camera.*` and `image.*` entities or direct HTTP(S) image URLs.
- Added crop-to-fill and fit-whole-image controls with a live 1-bit X3/X4
  preview using the same e-paper dithering as the device render.
- Added bounded image downloads, format and dimension validation, and explicit
  credential isolation so the Home Assistant bearer token is never attached to
  direct external image URLs.
- Direct URL image tiles remain usable when Home Assistant entity access is not
  configured.

## 0.12.0

- Added priority alert pages driven by Home Assistant entity state, including
  equals, threshold, on/off, contains, and unavailable conditions.
- Added optional alert expiry based on the entity's latest state change, so a
  persistent condition can restore the normal playlist and rearm when it
  changes again.
- Added timezone-aware scheduled page sets with overnight-window support.
- Added Dashboard Studio controls and doorbell, alarm, daytime energy,
  appliance-running, and weather-alert starter templates.
- Added active page-set telemetry through the bridge device API and response
  headers.

## 0.11.1

- Fixed the Home Assistant ingress entry so Dashboard Studio opens at
  `/studio/` instead of the invalid `//studio/` path.
- Added server-side normalization for doubled leading slashes to keep the web
  GUI usable while Home Assistant refreshes add-on metadata.

## 0.11.0

- Adds Dashboard Studio to the Home Assistant App with a responsive editor and
  live XTEINK X3/X4 e-ink preview.
- Persists visual profiles in the App data directory without replacing
  hand-maintained YAML configuration.
- Adds single, stacked, side-by-side, automatic, and four-tile page layouts.
- Adds per-tile entity selection, labels, units, semantic icons, gauges,
  progress bars, 24-hour history sparklines, and QR codes.
- Adds authenticated profile, entity-catalogue, preview, and assignment APIs.
- Queues a refresh for every device using a saved profile.
- Exposes Dashboard Studio through the Home Assistant App web UI and ingress.

## 0.10.8

- Bundles the credential-free shared X3/X4 FlexDisplay `0.14.0` firmware
  manifest for Home Assistant OTA updates.
- Migrates only the exact packaged `0.13.0` manifest so existing installations
  receive the new release without overwriting custom firmware URLs.
- Includes reliable device response-header handling, replay-safe OTA version
  checks, and physical-button Bridge check-ins in the firmware image.

## 0.10.7

- Maps new physical Right/Down button events to the next dashboard page and
  Left/Up events to the previous page.
- De-duplicates retried button telemetry so a single press advances only once.
- Keeps explicit Home Assistant navigation commands higher priority when they
  arrive in the same device check-in.

## 0.10.6

- Extend guarded USB recovery verification to an active fleet installation.
- Preserve canary state while auditing and clearing a verified fleet command.

## 0.10.5

- Redeliver unacknowledged durable commands with their existing command ID.
- Recover OTA installs when a device resets or loses the first command response.

## 0.10.4

- Stops sending local `device.*` telemetry placeholders to the Home Assistant
  REST API, preventing false fleet-wide `HA ERROR` banners.
- Allows the USB-recovery API to accept recent macOS USB evidence when an X4's
  charge-detection telemetry reports disconnected despite an active serial
  connection.
- Validates and audits the matching USB serial suffix, modem port, full-flash
  backup SHA-256, and observation timestamp.

## 0.10.3

- Added an explicit USB-recovery verification endpoint and Home Assistant
  button for reconciling a stuck firmware canary.
- Requires a recent check-in from the same canary, the exact configured target
  firmware, USB power, a ready SD card, no pending commands, and the matching
  durable install command ID.
- Records USB recovery evidence and verification method in bounded device and
  rollout audit histories instead of forging a device acknowledgement.

## 0.10.2

- Cancels legacy pending firmware installs during migration because they predate
  the canary gate and cannot be safely resumed.
- Preserves non-install legacy commands with newly assigned durable IDs.
- Records cancelled legacy install metadata for audit and recovery.

## 0.10.1

- Migrates pre-command-ID Bridge state on startup.
- Preserves legacy pending commands by assigning durable IDs.
- Clears unacknowledgeable legacy dispatched commands so stale installs cannot
  permanently block the canary and parallel-install safety gates.

## 0.10.0

- Added persistent command IDs and explicit server acknowledgement so device
  results are retained until the Bridge confirms the matching command.
- Added strict OTA manifest, SD-card, power, battery, and concurrent-install
  preflight checks.
- Added USB-powered canary-first rollout gating. Fleet installs remain blocked
  until the canary boots the target firmware and reports a matching completion.
- Added firmware rollout and per-device update-status entities and update-dialog
  diagnostics in Home Assistant.
- Bundled the credential-free shared X3/X4 FlexDisplay 0.13.0 firmware metadata.

## 0.9.0

- Added full queued remote control: force redraw, clear display, timed sleep,
  power-off, restart, navigation, refresh, and pending-command cancellation.
- Added Home Assistant controls for live polling, auto-start, intelligent
  sleep, USB stay-awake, active hours, refresh and sleep durations, battery
  thresholds, interval multipliers, name, area, and timezone.
- Added live-mode fleet provisioning and remote-command sleep plans.
- Expanded optional MQTT Discovery buttons to match the device command set.
- Bundled the credential-free shared X3/X4 FlexDisplay 0.12.0 firmware metadata.

## 0.8.0

- Added intelligent sleep plans with active hours, low-battery throttling,
  critical-battery shutdown, and USB stay-awake behavior.
- Added unchanged-image hashing so devices skip unnecessary e-paper refreshes
  and lengthen their next sleep.
- Added sleep action, reason, duration, next-wake, and unchanged-image entities.
- Added automatic Home Assistant entity discovery when a new fleet device
  checks in after integration setup.
- Bundled the credential-free FlexDisplay 0.11.0 application image metadata.

## 0.7.0

- Added persistent zero-touch device provisioning.
- Added per-device name, area, dashboard profile, assigned mode, refresh
  interval, and auto-start policy.
- Added provisioning response headers for FlexDisplay 0.10.0.
- Bundled credential-free FlexDisplay 0.10.0 application-image metadata for
  safe public OTA distribution.

## 0.6.0

- Added configurable dashboard profiles with per-device assignment.
- Added previous, overview, and direct page-selection controls.
- Added optional automatic profile rotation.
- Bundled the verified shared X3/X4 FlexDisplay 0.8.0 application image metadata.

## 0.5.0

- Expanded the built-in dashboard set from four to eight readable pages.
- Added dedicated Temperatures, Humidity, Batteries, Power, Device Health, and
  Connectivity pages.

## 0.4.1

- Added DejaVu scalable fonts to the Home Assistant App image so the large
  dashboard typography renders correctly in production.

## 0.4.0

- Added Overview, Climate, Energy, and Device Status dashboard pages.
- Made the Home Assistant Next Screen button advance a persistent page index.
- Increased dashboard value, label, and icon sizes for X3/X4 readability.
- Added current dashboard page and page-number sensors.

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
