# Compatibility matrix

Update this table in every release that changes a protocol or minimum version.

| Component | Current known version | Compatibility notes |
| --- | --- | --- |
| FlexDisplay platform | 0.44.0 | Bridge, Studio and HA integration are version-locked |
| Echo Spot receiver | 0.2.0 | Original 2017 `rook`; LineageOS 18.1 / Android 11 |
| X3/X4 packaged firmware | 1.5.0-flexdisplay.0.38.1 | Canary and USB gates remain mandatory |
| Note 4 packaged firmware | 1.2.2-voice-remote | Distributed from the Bridge package |
| Home Assistant | Home / Dumb at `10.200.40.4` | Bridge API on port 8099; no credentials belong here |

Protocol changes must document both the minimum compatible device version and
the fallback behavior for older devices.
