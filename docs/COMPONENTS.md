# Component ownership

| Product name | Repository location | Release unit | Primary responsibility |
| --- | --- | --- | --- |
| FlexDisplay Bridge | `flexdisplay_bridge/flexdisplay_bridge/` | Platform | Rendering, device state, commands, media, fleet and Home Assistant API |
| Flex Studio | `flexdisplay_bridge/flexdisplay_bridge/static/` | Platform | Dashboard, content and fleet authoring UI |
| Home Assistant integration | `custom_components/flexdisplay/` | Platform | Home Assistant devices, entities, services and update surfaces |
| Android receiver | `rook_receiver/` | Receiver | Echo Spot 480 × 480 and Echo Show 5 960 × 480 Android kiosk, touch, alerts and telemetry |
| FlexHub | External `xteink-flexhub` repository | Firmware | Always-on relay and local fleet transport |
| X3/X4 firmware | External `xteink-flexdisplay` repository | Firmware | Embedded e-paper runtime |
| Planned ESP colour/LVGL receiver | External authoritative `xteink-flexdisplay` repository after target merge | Firmware | Proposed embedded colour display runtime, LVGL rendering, touch/input and authenticated Bridge transport; no target is present on authoritative main yet |
| Factory/release kit | External `xteink-flexdisplay-release` repository | Release train | Coordinated images and provisioning artifacts |

Keep Bridge, Studio and the Home Assistant integration in this monorepo. Split
a component only when it has an independent owner, CI pipeline and release
lifecycle, not merely because it has a separate product name.

## Receiver-family contract

The platform owns the bounded profile/screen schema, Studio authoring surface,
capability negotiation, Bridge transport, package metadata, and fail-closed
routing rules. An external receiver repository owns its board definitions, pin
maps, bootloader and partition layout, hardware drivers, runtime source, target
build, and model-specific recovery procedure. Do not copy external firmware
source into this repository to avoid establishing its ownership.

Before a receiver family or protocol revision can be reviewed, record:

- its authoritative Forgejo repository and immutable family/board identifier;
- its versioned, size/depth/count-bounded schema and capability vocabulary;
- minimum compatible Bridge and receiver versions plus explicit fallback;
- authenticated manifest, command and event scope;
- artifact type, partition/write scope, provenance fields, and recovery method;
- negative tests proving other families reject its images, commands and OTA
  routes and that an untrusted or user-editable model label cannot select them.

Identity-ambiguous receivers remain read-only. Unsupported fields, excessive
payloads, unknown actions, incompatible schema versions, and cross-family
firmware requests fail closed rather than being forwarded or guessed.
