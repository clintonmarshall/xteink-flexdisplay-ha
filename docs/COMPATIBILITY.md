# Compatibility matrix

Update this table in every release that changes a protocol or minimum version.

| Component | Current known version | Compatibility notes |
| --- | --- | --- |
| FlexDisplay platform | 0.46.0 | Bridge, Studio and HA integration are version-locked |
| Echo Spot receiver | 0.5.0 | Original 2017 `rook`; LineageOS 18.1 / Android 11; supports push-to-talk Assist, Android fleet controls, and hardware capability telemetry |
| Echo Show 5 receiver | 0.5.0 | 2019 `checkers`; LineageOS 18.1 / Android 11; supports push-to-talk Assist, Android fleet controls, and hardware capability telemetry |
| X3/X4 packaged firmware | 1.5.0-flexdisplay.0.39.0 | Official Home Assistant OpenDisplay discovery and image upload; X3/X4 USB, BLE upload, persistent receiver, refresh and reconnect canaries passed |
| Note 4 packaged firmware | 1.2.2-voice-remote | Distributed from the Bridge package |
| Planned JC3636 colour/LVGL receiver | Unreleased design; not present on authoritative main | Target work is being developed for the external `xteink-flexdisplay` repository; no packaged artifact, Bridge compatibility claim, or physical display/touch canary yet |
| Home Assistant | Minimum supported release not yet recorded | Bridge API uses TCP port 8099; verify and record the minimum tested release before changing compatibility |

Deployment-specific hostnames, IP addresses, environment labels, and credential
labels belong in the approved host/service inventory, not in this product
compatibility matrix.

Protocol changes must document the owning repository, immutable receiver family
and board identifier, artifact type, minimum compatible Bridge and receiver
versions, bounded schema version, capability negotiation, explicit fallback or
fail-closed behaviour, and recovery method. Unrelated receiver families must
reject incompatible images, commands, actions, and OTA routes. A user-editable
model string is not trusted compatibility or firmware-routing evidence.

Platform 0.46.0 introduces a trusted capability contract shared by the Bridge,
Studio, MQTT, and the Home Assistant integration. Legacy X3/X4 check-ins remain
accepted; devices without trusted family or capability evidence are presented
read-only and excluded from firmware, provisioning, policy, and command actions.
Packaged device firmware versions are unchanged.

Platform 0.46.0 content packs are immutable and use the exact opaque-token
manifest and file URLs advertised by the Bridge. Current packaged X3/X4
firmware follows those URLs without a firmware update. A legacy client that
discards URL query parameters cannot fetch managed packs and reports a content
error; normal dashboard delivery remains available. Pack downloads use local
HTTP, so sensitive Quick Cards belong only on a trusted display LAN unless a
TLS proxy or private tunnel protects that traffic.

Android receiver `0.5.0` adds explicit capability headers for camera,
microphone, audio, touch, always-on display class, device class, and screen
resolution. Older receivers remain compatible: the Bridge falls back to
inferring touch, colour, audio, microphone, and always-on state from the
existing comma-separated `X-FlexDisplay-Capabilities` header where possible,
and reports unsupported or unknown capability fields as false/unknown.

Home Assistant OpenDisplay uploads use BLE. Assign OpenDisplay as the device's
persistent mode for on-demand uploads; temporary Quick Menu sessions retain a
bounded receive window. When USB and Wi-Fi are active, select `ble_only` because
the `auto` transport policy prefers the LAN receiver.
