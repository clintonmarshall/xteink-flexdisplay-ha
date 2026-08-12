# Compatibility matrix

Update this table in every release that changes a protocol or minimum version.

| Component | Current known version | Compatibility notes |
| --- | --- | --- |
| FlexDisplay platform | 0.45.0 | Bridge, Studio and HA integration are version-locked |
| Echo Spot receiver | 0.2.0 | Original 2017 `rook`; LineageOS 18.1 / Android 11 |
| Echo Show 5 receiver | 0.2.0 | 2019 `checkers`; LineageOS 18.1 / Android 11 |
| X3/X4 packaged firmware | 1.5.0-flexdisplay.0.39.0 | Official Home Assistant OpenDisplay discovery and image upload; X3/X4 USB, BLE upload, persistent receiver, refresh and reconnect canaries passed |
| Note 4 packaged firmware | 1.2.2-voice-remote | Distributed from the Bridge package |
| Home Assistant | Home / Dumb at `10.200.40.4` | Bridge API on port 8099; no credentials belong here |

Protocol changes must document both the minimum compatible device version and
the fallback behavior for older devices.

Home Assistant OpenDisplay uploads use BLE. Assign OpenDisplay as the device's
persistent mode for on-demand uploads; temporary Quick Menu sessions retain a
bounded receive window. When USB and Wi-Fi are active, select `ble_only` because
the `auto` transport policy prefers the LAN receiver.
