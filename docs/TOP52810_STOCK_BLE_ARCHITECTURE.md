# TOP52810M-D01 stock BLE architecture

Status: architecture candidate; external and read-only until the admission gates
in this document are complete.

This record defines how FlexDisplay may support the verified
`TOP52810M-D01` / `MS136F6 V1.0` e-paper tag without describing it as an
OpenDisplay receiver. It does not admit the family for upload, firmware,
provisioning, policy, reset, or other command actions in a released platform.

## Product placement

Studio and fleet views should present this family under **Compact e-paper
tags**, with a **Stock BLE** transport label. `OpenDisplay` remains the official
X3/X4 receiver transport and its existing `supports_opendisplay` and transport
policy capabilities must not be reused for this vendor protocol.

The canonical identifiers proposed for the admitted profile are:

| Field | Value |
| --- | --- |
| Family | `stock_ble_eink_tag` |
| Model key | `top52810m_d01_stock` |
| Display profile | `top52810m_d01_stock` |
| User label | `TOP52810M-D01 (stock BLE)` |
| Transport adapter | `top52810_stock_ble` |

These names describe the verified stock-firmware behavior. Patched or
replacement firmware is a separate family and admission decision even when it
runs on the same PCB.

## Ownership

| Concern | Owner |
| --- | --- |
| Device/profile contract, compact rendering, durable jobs, history and Studio UI | FlexDisplay platform |
| BLE discovery, connection and writes through Home Assistant Bluetooth and ESPHome Bluetooth proxies | FlexDisplay Home Assistant integration |
| Pure two-plane codec and serialized protocol state machine | Reusable transport-neutral library selected in the implementation task |
| Stock firmware | Device vendor; never packaged, updated or claimed by FlexDisplay |
| Patched/replacement firmware and recovery artifacts | Separate private firmware/recovery project |

The Bridge remains the fleet and rendering authority. It must not open its own
competing BLE scanner when Home Assistant owns Bluetooth and proxy routing. The
integration executes an authorized pending transfer and reports bounded state
back to the Bridge.

## Verified hardware and display capability

One physically tested unit has the following evidence:

| Capability | Verified value |
| --- | --- |
| External model | `TOP52810M-D01` |
| PCB | `MS136F6 V1.0` |
| MCU/radio | Nordic-compatible `NS52810 QCAA0`; nRF52810 memory map |
| Panel | `HINK-E029A10-A3`, 128 x 296 portrait |
| Palette | Black, white and red; red has physical precedence |
| Inputs | No button; one LED |
| NFC | Not present on the tested unit |
| Power | 3 V CR2450 pack; no verified battery telemetry |

An admitted capability descriptor must report a 128 x 296 portrait e-paper
display, no touch, no buttons, no frontlight, a black/white/red palette and a
periodic BLE receive window. It must not infer battery telemetry, wake support,
sleep configuration, firmware management or OTA support.

Another unit with the same external label is not automatically compatible.
Admission requires the exact PCB/MCU/panel scope, service shape and a bounded
diagnostic image transfer to match this record.

## Stable identity and admission

The observed advertisement name `TRSEPD_<suffix>`, manufacturer identifier
`0x1A28`, RSSI and visual proximity are discovery hints only. The macOS
CoreBluetooth observation ID used during reverse engineering is local to that
host and is not a portable fleet identity.

Before an upload action can be implemented, a hardware validation task must
establish one of these identities, in priority order:

1. a persistent EUI or serial read from authenticated device data;
2. a demonstrably persistent BLE identity exposed by every supported Home
   Assistant scanner/proxy path; or
3. an explicitly experimental, adapter-bound enrollment record created during
   one physically controlled advertising window.

The third option must not roam between adapters and must be labelled
experimental. Automatic enrollment by device name, manufacturer identifier or
RSSI is forbidden. Ambiguous or changed identity leaves the observation
read-only and cancels any pending connection.

Admission also requires the exact vendor service and characteristics:

- service `00000200-1212-EFDE-1523-785FEF13D123`;
- notification characteristic `00000204-1212-EFDE-1523-785FEF13D123`;
- write characteristic `00000205-1212-EFDE-1523-785FEF13D123`.

Unexpected services, characteristic properties, negotiated write limits or
notifications fail closed.

### Read-only identity experiment, 2026-09-02 to 2026-09-04

The intended Home Assistant controller is a virtual host with no local
Bluetooth adapter and six online remote adapters. Across separate days and
battery cycles, four independent ESPHome Bluetooth proxies exposed the same BLE
address, advertisement name, manufacturer identifier, manufacturer payload and
complete raw advertisement for the verified tag. All sources marked the
address as random; its two most significant bits match the Bluetooth
static-random class rather than a rotating private-address class. A later
observation beside the Blue proxy improved the received signal to -49 dBm
without changing any identity field.

After a battery cycle, a macOS scan also observed the same CoreBluetooth
identity and advertisement fields as the earlier sessions. macOS does not
reveal the underlying BLE address, so the independent Home Assistant proxy
observations provide the portable evidence.

The portable identity gate is **complete for this exact unit, Home Assistant
controller and ESPHome proxy path**. Enrollment may use the static-random BLE
address as the primary controller-scoped key, with the advertisement name,
manufacturer identifier and manufacturer payload as required cross-checks.
These cross-checks are not unique identity on their own. Connection-time
service and characteristic verification remains mandatory before any write.
Firmware replacement, a changed BLE address or a different physical unit
requires a new physically confirmed enrollment.

## Rendering contract

The generic dashboard renderer currently assumes a minimum width of 240
pixels. The tag therefore needs a dedicated compact renderer rather than being
routed unchanged through the existing generic screen endpoint.

The renderer produces a logical 128 x 296 three-colour canvas. A pure codec
then emits two 4,736-byte planes, with 16 bytes per row. For each row it packs
pixels most-significant bit first, reverses the 16-byte row order, and reverses
the bits in each byte. Black uses controller bit `0`; red uses controller bit
`1`.

The profile must expose `arbitrary_full_canvas: false` and these approximate
physical unsafe regions for black or white detail:

| X | Y |
| --- | --- |
| `40..119` | `153..162` |
| `40..119` | `173..182` |
| `40..119` | `193..202` |
| `40..119` | `213..222` |
| `40..119` | `233..242` |

Stock firmware overwrites those black-plane bands with a diagonal vendor
pattern. Studio should warn or reject templates that place text, black detail
or white detail there. A solid red fill is permitted because it physically
masks the forced black-plane pattern. Preview must show the limitation rather
than promising an unrestricted canvas.

## Delivery contract

The adapter performs exactly 44 serialized writes with response, notifications
enabled before the first request, and no more than 244 attribute-value bytes
per write:

1. session request and exact `30 34 00 00 00 00` notification;
2. black prepare and exact `31 31` notification;
3. twenty black data frames;
4. red prepare and exact `32 31` notification;
5. twenty red data frames; and
6. refresh request and exact `34 31` notification.

Each transfer is bound to the admitted identity, rendered-content SHA-256,
complete transfer-plan SHA-256, frame count and expiry. The adapter advances
only after the expected write acknowledgement or control notification. Busy,
unexpected, missing or out-of-order responses, a changed locator, disconnect,
or insufficient write size fail the attempt without trying a different nearby
tag.

The stock device normally exposes an unconnected receive window for roughly
24-30 seconds every 5.5 minutes. The queue should coalesce superseded images so
only the newest authorized image remains pending. A normal delivery may take
up to the next receive window plus approximately 6-8 seconds for transfer and
additional panel settling time.

Use these externally visible states:

- `queued` - a validated render exists;
- `waiting_for_window` - the exact device is asleep or not advertising;
- `transferring` - the bound 44-write plan is in progress;
- `refresh_started` - exact `34 31` was received;
- `physically_unverified` - radio delivery completed but no person or sensor
  verified the panel;
- `failed` - a terminal identity, protocol, expiry or transport error occurred.

`refresh_started` is not physical display success. Sleeping between expected
windows is not offline. The first implementation should declare the device
unavailable only after at least two missed expected intervals, while preserving
the last observation time and the reason separately.

## Security boundary

No authenticated or encrypted application protocol has been established.
Treat stock BLE delivery as a local, proximity-reachable and potentially
spoofable transport. Do not render secrets or sensitive notifications to this
family. Enrollment is explicit, writes are restricted to an admitted identity
and exact service shape, and only one bounded image-transfer job may be active
per tag.

Home Assistant owns scanner and proxy selection. The Bridge sends no BLE
locator to an untrusted client and stores no Home Assistant credentials in a
render job. Logs retain hashes, state transitions and non-secret capability
evidence, but not private image payloads unless screen history was explicitly
enabled for the device.

## Compatibility, fallback and recovery

Older platform versions and unknown TOP revisions treat the tag as unknown and
read-only. They must not fall back to X3/X4, generic e-paper, OpenDisplay or
firmware-management behavior.

A failed or expired transfer leaves the bistable panel showing its last good
image and returns the job to a bounded failure state; it must not loop
continuously. Visual rollback is another complete, independently authorized
image upload. Firmware restore does not restore the visible image.

Firmware backup, patching, flashing and recovery are outside this architecture
candidate. The tested unit has independently verified private flash and UICR
recovery artifacts, but their presence does not authorize platform firmware
actions or establish compatibility for another unit.

## Admission and implementation phases

1. **Architecture:** merge this ownership, identity, capability, transport,
   validation and recovery boundary. Family remains external/read-only.
2. **Portable identity experiment (complete for the canary):** identity remained
   stable across separate days, battery cycles and four ESPHome proxy sources
   on the intended Home Assistant controller.
3. **Pure codec tests:** add golden 128 x 296 plane and 44-frame fixtures from
   non-sensitive, physically verified evidence.
4. **Compact rendering:** add a built-in preview/profile with unsafe-region
   metadata and deterministic black/white/red output.
5. **One-tag transport canary:** implement Home Assistant-owned BLE delivery,
   durable Bridge job states, exact response checks and one named hardware
   canary. This phase requires a fresh device-write confirmation.
6. **Family admission:** update the released compatibility matrix only after
   the identity, security, renderer, transport, physical image and recovery
   gates pass. Firmware and administrative controls remain absent.
