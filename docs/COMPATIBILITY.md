# Compatibility matrix

Update this table in every release that changes a protocol or minimum version.

| Component | Current known version | Compatibility notes |
| --- | --- | --- |
| FlexDisplay platform | 0.46.0 | Bridge, Studio and HA integration are version-locked |
| Echo Spot receiver | 0.3.0 | Original 2017 `rook`; LineageOS 18.1 / Android 11; supports push-to-talk Assist |
| Echo Show 5 receiver | 0.3.0 | 2019 `checkers`; LineageOS 18.1 / Android 11; supports push-to-talk Assist |
| X3/X4 packaged firmware | 1.5.0-flexdisplay.0.39.0 | Official Home Assistant OpenDisplay discovery and image upload; X3/X4 USB, BLE upload, persistent receiver, refresh and reconnect canaries passed |
| Note 4 packaged firmware | 1.2.2-voice-remote | Distributed from the Bridge package |
| Home Assistant | Release evidence | Record the exact tested Core version with each release; hostnames and credentials do not belong here |

Protocol changes must document both the minimum compatible device version and
the fallback behavior for older devices.

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

Home Assistant OpenDisplay uploads use BLE. Assign OpenDisplay as the device's
persistent mode for on-demand uploads; temporary Quick Menu sessions retain a
bounded receive window. When USB and Wi-Fi are active, select `ble_only` because
the `auto` transport policy prefers the LAN receiver.
