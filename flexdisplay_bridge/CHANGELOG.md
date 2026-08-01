# Changelog

## 0.34.1

- Deliver server-rendered dashboards, mixed content, photo frames, and restored
  screens as native 1-bit BMP files to X3 devices, avoiding unreliable PNG
  inflate allocations while retaining compressed PNG delivery on X4 devices.
- Calculate cache hashes, ETags, unchanged responses, and transfer metrics from
  the exact payload delivered to each hardware model.
- Record bounded image-conversion diagnostics reported by firmware, expose the
  latest detail through the fleet API and MQTT, and clear the active fault once
  the device confirms a valid cached image.
- Bundle shared X3/X4 firmware `1.5.0-flexdisplay.0.34.1`, which lends display
  scratch memory only during PNG/JPEG conversion and reports precise decoder
  failures such as `png:inflate_init_failed`.
- Install dashboard files atomically, retain the previous valid screen when a
  transfer or conversion fails, and recover an interrupted cache swap after a
  reboot.

## 0.34.0

- Added persistent Mixed Content channels that combine a device's assigned Home
  Assistant dashboard pages with large-format Message, Quote of the Day, and
  RSS/Atom news screens.
- Added offline deterministic daily quotes, optional custom quote collections,
  Australian feed presets, bounded feed downloads, caching, stale-on-error
  behavior, and high-contrast X3/X4 rendering with optional QR links.
- Added the Content Channels workspace to Dashboard Studio for ordering,
  previewing, assigning, and refreshing mixed-content playlists.
- Added the on-device Quick Menu. Hold Confirm in Home Assistant mode to jump
  between dashboards, messages, news, quotes, OpenDisplay, Photo Frame, TRMNL,
  Reader, the full app menu, or sleep; short Confirm continues to refresh.
- Added page title, content type, selection, and position response metadata plus
  the `quick-menu` and `mixed-content` capability negotiation.
- Bundled shared X3/X4 firmware `1.5.0-flexdisplay.0.34.0` and reserved long
  Confirm in the physical-button editor so fleet mappings cannot hide the menu.

## 0.33.1

- Reject identity-less display check-ins and automatically purge the legacy
  `UNKNOWN` fleet record, including retained MQTT Discovery data and active
  rollout references.
- Added a permanent device removal action that clears both the Bridge registry
  and Home Assistant MQTT entities. A genuine display is discovered again if it
  later checks in with its stable identity.
- Expanded the Fleet Management device list, added battery percentage, and
  replaced ambiguous policy states with Applied, Waiting for wake, Confirming,
  Needs review, and Unmanaged labels.
- Fixed Select all and Clear by switching directly to selected-device scope.
- Hide completed firmware progress bars while preserving the completion audit
  and status details.

## 0.33.0

- Added the Fleet Management workspace for reusable policy profiles, scoped
  assignments, acknowledgement status, and safe canary-first OTA rollout.
- Added FlexHub configuration and health visibility, including network,
  storage, fleet, and Meshtastic status.

## 0.32.1

- Bundled the validated X3/X4 `0.32.0` OTA binary inside the Home Assistant App
  image so private GitHub repositories do not block firmware mirroring.
- The Bridge now verifies and seeds its local firmware cache from the packaged
  binary before attempting an external download. Custom firmware manifests
  continue to use their configured HTTP(S) source.

## 0.32.0

- Added revisioned fleet policy profiles for Battery Saver, Balanced, and USB
  Kiosk operation, with atomic X3/X4 scope selection and durable device
  acknowledgement.
- Added fleet-wide default application, dashboard profile, and Photo Frame
  album assignment through the Bridge API and SenseCAP FlexHub.
- Added per-device fleet policy selection plus desired/reported revision and
  synchronization diagnostics to both the optional custom integration and the
  App-only MQTT Discovery path.
- Added a native FlexHub Policies page and responsive web controls for fleet
  health, policy scope, content assignment, refresh, and immediate rollout.
- Added shared X3/X4 firmware `1.5.0-flexdisplay.0.32.0` with policy revision
  persistence and acknowledgement on successful check-in.

## 0.31.0

- Rebased the common X3/X4 firmware on CrossPoint 1.5 and the FreeInk hardware
  stack while preserving Reader, FlexDisplay modes, fleet identity, orientation,
  power policy, and factory provisioning.
- Added OpenDisplay 2.20-compatible direct zlib and ordered PIPE transfers,
  transfer watchdog recovery, and current protocol capability reporting while
  retaining FlexDisplay RLE compatibility.
- Updated the TRMNL/Terminus client to current request headers and display
  response fields, including charging, USB power, wake time, compatibility,
  temperature profile, and special-function metadata.
- Published the coordinated FlexDisplay Platform 0.31 release manifest and
  shared firmware `1.5.0-flexdisplay.0.31.0`.

## 0.30.0

- Published shared X3/X4 firmware `1.4.1-flexdisplay.0.30.0`.
- Added persistent default applications and optional USB keep-awake behavior.
- Added FlexShare receiver reservations, saved fleet targeting, retry reports,
  CRC-based unchanged-screen skipping, and negotiated RLE compression.
- Moved private FlexDisplay OpenDisplay commands to `0xE0` and `0xE1` to avoid
  collisions with official OpenDisplay 2.x commands while retaining staged
  sender fallback for older fleet devices.
- Defaulted browser-prepared frames to sharp monochrome, with photo dithering
  remaining available as an explicit option.

## 0.24.0

- Added per-boot reset telemetry with stable boot identifiers and explicit
  power-on, software, panic, watchdog, deep-sleep, brownout, and external
  reset reasons from the shared X3/X4 firmware.
- Added bounded check-in, reset, battery, Wi-Fi, SD, uptime, and free-memory
  history for every device.
- Added Wi-Fi trend, estimated missed check-ins, SD failure counters, watchdog
  counters, battery drain rate, and estimated battery runtime diagnostics.
- Expanded Fleet Health with battery and Wi-Fi sparklines, reset reason,
  check-in reliability, SD failures, and the active OTA maintenance window.
- Added complete MQTT Discovery diagnostics and problem sensors for overdue
  check-ins, watchdog/panic/brownout resets, and repeated SD failures.
- Added optional timezone-aware firmware maintenance windows with overnight
  schedules and a configurable USB-powered override. The feature defaults off
  for backwards-compatible manual rollouts.
- Added startup/check-in repair for successful OTA records that retained stale
  verification evidence from an earlier USB recovery.
- Added shared X3/X4 firmware `1.4.1-flexdisplay.0.24.0`.

## 0.23.1

- Fixed the first-canary USB safety gate after a rollout reset. A reset rollout
  now continues to require positive device-reported USB power before accepting
  its first firmware installation.
- Fixed successful OTA acknowledgements retaining an older USB-recovery
  timestamp and method. Exact-version boot acknowledgements now record their
  current verification time, `device_checkin` method, and command-history
  evidence.
- Added a regression test that verifies a fully charged, SD-ready device cannot
  bypass the USB canary requirement after the previous rollout is archived.

## 0.23.0

- Added capability-negotiated zero-byte responses when a device confirms that
  the matching dashboard image is physically cached on its SD card.
- Added PNG Photo Frame delivery for upgraded X3/X4 devices while preserving
  BMP responses for earlier firmware.
- Added per-device transfer encoding, delivered bytes, saved bytes, and
  savings-percentage diagnostics through MQTT Discovery.
- Added shared X3/X4 firmware `1.4.1-flexdisplay.0.23.0` with cache-safe
  capability advertisement, PNG conversion, and transfer telemetry parsing.

## 0.22.3

- Added per-tile text sizing from 60% to 180% with immediate X3/X4 preview
  updates.
- Added independent QR-code sizing from 50% to 150% so codes can fill a
  single-page layout or remain compact in mixed dashboards.
- Reworked QR tiles to reserve separate label, code, and caption regions,
  preventing enlarged codes from colliding with text.
- Added larger, readable defaults to the QR Page and ID Badge templates while
  preserving 100% defaults for existing profiles.
- Made physical-button activation explicit in Studio with Ready/Waiting mode
  status, Bridge save time, last execution result, and clearer save feedback.
- Added optional e-paper button indicators: dotted for short press,
  double-dotted for double press, and solid for long press. Indicators identify
  Left, Up, Confirm, Down, and Right without covering dashboard tiles.

## 0.22.2

- Added a QR Code Page template with a full-page, single-tile layout.
- Added guided QR builders for website links, plain text, Wi-Fi networks,
  LinkedIn profiles, contact cards, email messages, and phone numbers.
- Added safe profile-photo uploads for standalone ID badges. Photos are
  normalized, cropped, dithered, and stored in Bridge-managed data.
- Added Classic, Bold Band, Diagonal, and Halftone ID badge themes designed
  for high-contrast X3/X4 e-paper output.

## 0.22.1

- Added fixed-content Dashboard Studio tiles that render without a Home
  Assistant entity or token.
- Added a Name Card / ID Pass visual and starter template with large,
  e-ink-readable name, role, and organisation fields.
- Extended QR tiles with a built-in text/URL editor so websites, plain text,
  Wi-Fi details, and contact-card content can be encoded directly.
- Standalone pages now show a `STANDALONE` status rather than implying a Home
  Assistant connection.

## 0.22.0

- Added a Fleet Content workspace to Dashboard Studio for validated ZIP
  uploads and multi-device rollout assignment.
- Added per-device desired, installed, pending, and failed content-pack state
  with durable acknowledgements at the next normal Bridge check-in.
- Added safe device-side staging, size and SHA-256 validation, managed-path
  restrictions, atomic file replacement, and rollback on installation errors.
- Added a private local LDCS Factory Kit builder with a shared X3/X4 full-flash
  image, one-time SD reset, default module settings, original sample images and
  eBooks, preseeded private Wi-Fi, checksums, and JSON/CSV flashing reports.
- Kept factory resets USB-only while allowing subsequent photos, books, logos,
  and asset revisions to roll out non-destructively through Fleet Manager.

## 0.21.0

- Added a Branded Fetch Screen designer to Dashboard Studio with fleet-default
  and per-device designs, exact X3/X4 previews, logo uploads, four layouts, and
  device, owner, area, and profile tokens.
- Added `always`, manual-wake, USB-only, and disabled display policies.
- The Bridge renders an exact one-bit BMP and advertises its URL and SHA-256 in
  the normal dashboard response. Firmware downloads it only when the design
  changes, validates it, and keeps it on the SD card for instant offline use.
- Added an atomic device cache update with checksum, BMP, and panel-dimension
  validation plus automatic fallback to the built-in fetching message.
- Bundled shared runtime-detected X3/X4 firmware `0.21.0`.
- Made packaged firmware option migration repair mixed release metadata, such
  as an old advertised version paired with a newer packaged URL and checksum,
  while preserving genuinely custom manifests.
- Fixed Home Assistant camera and image imports through the Supervisor Core
  API proxy so signed `/api/camera_proxy` paths retain the required `/core`
  prefix instead of returning HTTP 403.

## 0.20.0

- Added an App-only Home Assistant entity path using full MQTT Discovery for
  sensors, binary sensors, buttons, switches, numbers, selects, update
  controls, text/timezone settings, and physical-button events.
- Added the `home_assistant_entity_source` migration guard. Existing installs
  default to `hacs`, `mqtt` enables the App-only device, and `both` is reserved
  for short migration testing.
- Added a Fleet Health workspace with power, battery, Wi-Fi, firmware,
  dashboard, next-wake, and actionable problem states.
- Added bounded, durable X3/X4 screen history with current-screen previews and
  exact one-shot resend from either the API or Fleet Health.
- Added a retained MQTT Image entity for the current e-paper screen and native
  firmware-update progress in the MQTT Update entity.
- Added automatic release of stale OTA install commands after a configurable
  timeout while preserving failure and rollout audit evidence.
- Existing shared X3/X4 firmware `0.19.0` remains compatible; this release does
  not require a USB reflash.

## 0.19.0

- Added Home Assistant controls to cancel active commands, retry failed
  firmware updates, reset a blocked rollout, and verify USB recovery.
- Cancellation now covers both queued and already-delivered durable commands,
  with device-side checks before validation and flashing.
- Added firmware stages and percentages for preflight, download, validation,
  flash, reboot, completion, cancellation, and exact failure reporting.
- Added bounded retry attempts and configurable backoff without weakening the
  canary-first rollout gate.
- Added automatic reconciliation when a USB-recovered device reports the exact
  target firmware at its next Bridge check-in.
- Added a Bridge-local firmware mirror that validates the configured file's
  exact byte size and SHA-256 before serving it to devices.
- Added bounded rollout audit history and Home Assistant firmware stage,
  progress, error, and error-time sensors.

## 0.18.0

- Added a persistent Dashboard Studio Photo Frame library with albums and
  per-device assignment.
- Added bounded JPEG, PNG, WebP, and BMP uploads plus Home Assistant
  `camera.*`/`image.*` snapshot capture.
- Added exact X3/X4 monochrome previews with crop/contain, rotation, captions,
  resize, and Floyd–Steinberg dithering.
- Added ordered or deterministic shuffled playback, configurable intervals,
  timezone-aware active windows, and wake-at-next-window scheduling.
- Added Bridge-served one-bit BMP playback with unchanged-image hashing,
  intelligent sleep, queued next/previous controls, and physical-button
  navigation.
- Extended the local SD Photo Frame activity to catalogue and convert JPEG and
  PNG files alongside BMP images.
- Added Home Assistant sensors for the current Photo Frame album, image, and
  album position.

## 0.17.0

- Replaced the browser-native Dashboard Studio entity list with a searchable
  full-catalogue picker that remains usable on large Home Assistant installs.
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
