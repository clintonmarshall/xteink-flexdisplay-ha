# Compatibility matrix

Platform 0.50.10: Content adds PNG/JPEG upload, Fit/Crop preview and
explicit send for the admitted F6ED tag. HA image actions also accept regular
files under /media. File actions require Bridge and integration 0.50.10;
older versions retain native-base64 actions without file conversion.
Tag admission and BLE timing are unchanged.

Platform 0.50.9: the TOP52810 F6ED canary supports hash-confirmed custom PNG
delivery through HA. Bridge and integration must both be at least 0.50.9;
older versions retain diagnostic-only actions and reject image payloads.
Live custom-image delivery through HA requires a separately verified canary.
See [custom-image actions](TOP52810_STOCK_BLE_ARCHITECTURE.md#custom-image-actions-platform-0509).

Platform 0.50.8: the admitted TOP52810 F6ED canary has a separate experimental
HA device page with status sensors and a hash-checked diagnostic-image button.
This is not general family admission or arbitrary image/firmware upload. See
the [stock-tag device-page contract](TOP52810_STOCK_BLE_ARCHITECTURE.md#home-assistant-device-page-platform-0508).

Platform 0.50.7 adds periodic checks for stock TOP52810 jobs queued after
Bluetooth discovery, using only recent connectable observations. Integrations
before 0.50.7 can miss late jobs when unchanged advertisements are deduplicated.
Exact target, plan, expiry, GATT/MTU and single-attempt requirements remain.
This is a software-only change; it does not establish physical proxy delivery
or extend the canary job deadline. Android and packaged firmware are unchanged.

Update this table in every release that changes a protocol or minimum version.
This document records released software compatibility, not live infrastructure.
Live hostnames, IP addresses, credentials, and current deployment observations
belong in inventory or release evidence, never in this table.

| Component | Current known version | Compatibility notes |
| --- | --- | --- |
| FlexDisplay platform | 0.50.10 | Bridge, Studio and HA integration are version-locked |
| Echo Spot receiver | 0.5.0 | Original 2017 `rook`; LineageOS 18.1 / Android 11; supports push-to-talk Assist, Android fleet controls, and hardware capability telemetry |
| Echo Show 5 receiver | 0.5.0 | 2019 `checkers`; LineageOS 18.1 / Android 11; supports push-to-talk Assist, Android fleet controls, and hardware capability telemetry |
| Android phone companion | 0.5.0-companion (version code 6; release candidate, unpublished) | Android 7.0+; foreground-only room endpoint with local camera and Dock consent; Companion-only signing and publication contract |
| Colour/LVGL receiver contract | v1 | Bridge and Studio contract available; JC3636 receiver firmware is separately versioned and is not packaged by the Platform release |
| X3/X4 packaged firmware | 1.5.0-flexdisplay.0.39.0 | Official Home Assistant OpenDisplay discovery and image upload; X3/X4 USB, BLE upload, persistent receiver, refresh and reconnect canaries passed |
| X4 Pro external firmware | Not released | Family identity/admission contract is merged on protected `main` in external Forgejo repository `clintonmarshall/xteink-flexdisplay` at exact commit `de032ebac1f68f743c43ac076cc1ff3e24576092`, path `firmware/docs/flex/X4_PRO_HARDWARE_ADMISSION.md`. The correctly bound hardware is S3 with 16 MiB flash and 8 MiB PSRAM and uses artifact family `x4pro_s3`, but this platform release contains no compatible manifest or artifact and advertises no install action. Any non-S3 or incomplete report remains read-only. |
| TOP52810M-D01 stock BLE candidate | Not admitted | Architecture-only 128 x 296 black/white/red compact tag. One `MS136F6 V1.0` unit has physical protocol evidence, but portable fleet identity and Home Assistant proxy transport remain admission gates. It is not OpenDisplay and exposes no upload, firmware, provisioning, policy, reset or command action. |
| Note 4 packaged firmware | 1.2.2-voice-remote | Distributed from the Bridge package |
| Home Assistant | No minimum declared | Bridge App and integration; each release must record the exact tested Home Assistant Core version in its release evidence |

Protocol changes must document both the minimum compatible device version and
the fallback behavior for older devices.

The TOP52810M-D01 architecture candidate is documented in
[`TOP52810_STOCK_BLE_ARCHITECTURE.md`](TOP52810_STOCK_BLE_ARCHITECTURE.md).
Its verified stock protocol is deliberately separate from OpenDisplay. Older
platforms and unknown revisions must treat it as an unknown read-only device;
the compatibility row is not release admission or authorization to connect or
write.

Only device families represented in this matrix and admitted through the
architecture process are supported release targets. A newly observed family
remains external and read-only until its owning repository, stable identity and
capability evidence, minimum versions and fallbacks, transport/security review,
hardware validation, and recovery path are documented. Unknown families must
not inherit firmware, provisioning, policy, reset, or command capabilities from
a visually similar or historically inferred model.

Platform 0.46.0 introduces a trusted capability contract shared by the Bridge,
Studio, MQTT, and the Home Assistant integration. Legacy X3/X4 check-ins remain
accepted; devices without trusted family or capability evidence are presented
read-only and excluded from firmware, provisioning, policy, and command actions.
Packaged device firmware versions are unchanged.

X4 Pro is an external, not-yet-released target. Its canonical model is
`X4_PRO` (`x4_pro` in platform APIs); it never falls back to generic X4.
Platform consumers that do not receive the exact board, hardware revision,
MCU, 16 MiB flash, 8 MiB PSRAM, and capability evidence must keep it
read-only. Studio recognizes `X4_PRO` as its own 480 × 800 preview target and
does not offer legacy X4 firmware or physical-button controls for it. Older
Bridge, Studio, MQTT, and Home Assistant consumers should treat `X4_PRO` as
unknown rather than offering X3/X4 firmware or management actions. The logical
480 × 800 preview alone does not establish device image ingestion, command,
provisioning, or OTA compatibility.

Platform 0.46.0 content packs are immutable and use the exact opaque-token
manifest and file URLs advertised by the Bridge. Current packaged X3/X4
firmware follows those URLs without a firmware update. A legacy client that
discards URL query parameters cannot fetch managed packs and reports a content
error; normal dashboard delivery remains available. Pack downloads use local
HTTP, so sensitive Quick Cards belong only on a trusted display LAN unless a
TLS proxy or private tunnel protects that traffic.

Platform 0.47.0 adds colour/LVGL receiver contract v1. Compatible receivers
must present a verified device identity, derived receiver credential, matching
hardware profile, and supported contract version before the Bridge will deliver
a bounded declarative manifest or accept an event. Existing e-paper and Android
receivers retain their established render paths. Unknown receivers, older LVGL
contracts, and profiles using unsupported widgets fail closed instead of being
treated as a similar device. JC3636 receiver firmware is owned and released
separately and is not included in this software-only Platform release.

Platform 0.47.1 changes only the protected Forgejo release path and advances
the behaviorally unchanged Companion candidate to version code 6. Bridge,
Studio, Home Assistant integration and packaged device firmware remain
compatible with 0.47.0; signed draft assets are now re-read through their
authenticated immutable attachment UUIDs before canary or publication.

Platform 0.48.0 adds revision-scoped X4 Pro capabilities to the Bridge,
Studio, MQTT and Home Assistant integration. Only an exact S3 report with the
admitted board ID, MCU family, 16 MiB flash, 8 MiB PSRAM and explicit capability
evidence receives the matching management surface; incomplete, incompatible or
non-S3 reports remain read-only. The release does not package or authorize X4
Pro firmware or an install action. Existing X3/X4, Note 4 and Android receiver
versions remain compatible and their packaged artifacts are unchanged.

Platform 0.49.0 adds the Bridge-rendered **Warm household** profile for the
existing rectangular Android Companion and Echo Show 5 receiver contracts. It
uses each receiver's established RGB PNG delivery path and native 1200 × 675
or 960 × 480 dimensions, so there is no protocol, minimum receiver version,
or Android source-version change. Older Bridges continue to render their
existing dashboards, while e-paper and LVGL receivers retain their existing
paths and reject this Android-only layout. Packaged X3/X4 and Note 4 firmware
artifacts are unchanged; this software-only release requires no flash or OTA.

Platform 0.50.0 adds a guarded stock-BLE canary for TOP52810M-D01 revision
`MS136F6 V1.0` at 128 x 296 black/red. Admission requires the complete observed
identity tuple: Bluetooth address `DF:84:6B:DE:F6:ED`, advertised name
`TRSEPD_F6ED`, manufacturer ID `0x1A28` with payload `ffffff00000d`, service
`00000200-1212-efde-1523-785fef13d123`, notification characteristic
`00000204-1212-efde-1523-785fef13d123`, and write characteristic
`00000205-1212-efde-1523-785fef13d123`. A mismatched or incomplete observation
stays read-only. Home Assistant Bluetooth/ESPHome proxies own BLE discovery and
the connection lifecycle; FlexDisplay owns rendering, durable job state,
bounded transfer policy, and physical-verification status. Older Bridges and
integrations do not expose this canary but remain compatible with all existing
device families. Packaged X3/X4 and Note 4 firmware and Android receiver
versions are unchanged, so this software-only release requires no flash, OTA,
or Android publication.

Platform 0.50.1 changes only the protected DumbHA rollout path. Bridge,
Studio, Home Assistant integration protocol, Android receiver contracts, and
all device-family behavior remain compatible with 0.50.0. Integration staging
and the Home Assistant Core restart are now distinct, tag-scoped operations
with independent confirmation and rollback evidence. Packaged X3/X4 and Note
4 firmware bytes are unchanged, so this software-only release requires no
flash, OTA, or Android publication.

Platform 0.50.2 updates only the guarded DumbHA integration-stage backup
verification so it accepts both the current Home Assistant Supervisor
`homeassistant` metadata field and the legacy `folders` list. The integration
swap and separately confirmed Core restart remain unchanged. Bridge, device,
Android, and packaged firmware behavior are identical to 0.50.1.

Platform 0.50.3 updates the protected DumbHA Core-restart verification path for
Home Assistant releases whose successful Core information response omits the
runtime state. The exact expected Core version is still required, and any
reported state must be `started`. It also adds a separately confirmed,
non-restarting reconciliation path bound to the exact failed workflow run and
pending integration-stage record. Bridge, device, Android, and packaged
firmware behavior are identical to 0.50.2.

Platform 0.50.4 corrects the protected reconciliation lineage for a pending
integration stage created by an earlier release. The failed restart workflow
must match that exact staged release and its recorded receiver, while the
repair workflow and installed command-gated receiver must match the current
release. A subsequent integration stage is accepted only when its matching
predecessor is already verified complete; mismatched or incomplete stages
remain blocked. Bridge, device, Android, and packaged firmware behavior are
identical to 0.50.3.

Platform 0.50.5 repairs Home Assistant current-screen image entities by
initializing both required entity bases and passing the Home Assistant instance
to the image entity. This restores its HTTP client and rotating access-token
state without changing the Bridge protocol, device behavior, Android receiver
versions, or packaged firmware bytes. Older clients retain their existing
fallback behavior and no minimum version changes.

Platform 0.50.6 fixes stock TOP52810 discovery for advertisements without
service UUIDs. The queued address, name and manufacturer payload remain exact
pre-connection checks; GATT service, characteristics and ATT MTU remain
mandatory pre-write checks after connection. The stock-BLE canary requires
integration 0.50.6 or later for this advertisement shape; older integrations
may leave jobs waiting until expiry. Other device protocols, Android receiver
versions and packaged firmware bytes are unchanged. Physical delivery remains
unverified until a separately approved canary test.

Android receiver `0.5.0` adds explicit capability headers for camera,
microphone, audio, touch, always-on display class, device class, and screen
resolution. Older receivers remain compatible: the Bridge falls back to
inferring touch, colour, audio, microphone, and always-on state from the
existing comma-separated `X-FlexDisplay-Capabilities` header where possible,
and reports unsupported or unknown capability fields as false/unknown.

Rectangular Android receivers accept the Bridge-rendered **Warm household**
RGB PNG at their existing screen dimensions: 1200 × 675 for Companion and
960 × 480 for Echo Show 5. This is a Bridge renderer/profile capability and
does not require a new Android receiver version. Older Bridges continue to
serve monochrome bitmap dashboards; e-paper and LVGL receivers retain their
existing render and validation paths.

The phone flavor derives `0.5.0-companion` (version code 6) from the shared
Android source version. It is an unpublished release candidate until a
protected Forgejo job signs one immutable APK, that exact checksum passes the
Galaxy canary, and the unchanged draft is published. The production
signing/publication contract is Companion-only; the Echo Spot and Echo Show
rows describe source and runtime compatibility and do not claim a
production-signed kiosk APK channel.

Home Assistant OpenDisplay uploads use BLE. Assign OpenDisplay as the device's
persistent mode for on-demand uploads; temporary Quick Menu sessions retain a
bounded receive window. When USB and Wi-Fi are active, select `ble_only` because
the `auto` transport policy prefers the LAN receiver.
