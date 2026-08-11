# Component ownership

| Product name | Repository location | Release unit | Primary responsibility |
| --- | --- | --- | --- |
| FlexDisplay Bridge | `flexdisplay_bridge/flexdisplay_bridge/` | Platform | Rendering, device state, commands, media, fleet and Home Assistant API |
| Flex Studio | `flexdisplay_bridge/flexdisplay_bridge/static/` | Platform | Dashboard, content and fleet authoring UI |
| Home Assistant integration | `custom_components/flexdisplay/` | Platform | Home Assistant devices, entities, services and update surfaces |
| Echo Spot receiver | `rook_receiver/` | Receiver | 480 × 480 Android kiosk, touch, alerts and telemetry |
| FlexHub | External `xteink-flexhub` repository | Firmware | Always-on relay and local fleet transport |
| X3/X4 firmware | External `xteink-flexdisplay` repository | Firmware | Embedded e-paper runtime |
| Factory/release kit | External `xteink-flexdisplay-release` repository | Release train | Coordinated images and provisioning artifacts |

Keep Bridge, Studio and the Home Assistant integration in this monorepo. Split
a component only when it has an independent owner, CI pipeline and release
lifecycle, not merely because it has a separate product name.
