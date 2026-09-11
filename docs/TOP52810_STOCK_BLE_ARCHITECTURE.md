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

### Advertisement compatibility (2026-09-08)

Home Assistant's Blue proxy observed `TRSEPD_F6ED` at
`DF:84:6B:DE:F6:ED` with manufacturer ID `0x1A28`, payload
`ffffff00000d`, and **no advertised service UUIDs**. Requiring the GATT
service UUID in the discovery matcher prevented the queued canary from being
claimed (three-minute expiry, zero transfer attempts).

Discovery therefore matches manufacturer ID and connectability, then checks
the queued exact address, name and manufacturer payload before claiming or
connecting. The required GATT service, write-with-response and notification
characteristics, and ATT MTU are still verified after connection and before
any write. Missing advertised UUIDs are not evidence of missing GATT services.
This software correction alone is not proof of a successful physical update.

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

The owner reports a five-minute receive window after battery connection
(2026-09-11). Earlier notes inferred 24-30 seconds every 5.5 minutes; that
periodic schedule is not established and must not be used as a delivery promise.
HA observation timestamps do not identify battery connection or firmware boot.
Transfer and panel settling also take time; queue expiry is a separate limit.

### Home Assistant device page (Platform 0.50.8)

`GET /api/v1/stock-ble/top52810/devices` is an authenticated read-only
summary of explicitly queued identities, including terminal jobs. It contains
no frames or leases, and does not enroll devices from advertisements.

HA creates **TOP52810 F6ED (experimental)** only for the admitted address
`DF:84:6B:DE:F6:ED`, name `TRSEPD_F6ED`, manufacturer `0x1A28`, payload
`ffffff00000d`. Other tags remain unadmitted. Stable device/entity identifiers
are scoped to config entry and address, separate from generic receiver and
firmware entity factories. The page contains:

- Bluetooth window: `advertising` for matching connectable observations up to
  ten seconds old. The observation-age candidate below adds `recently_seen`
  for older eligible cached sightings; neither state proves a live connection.
  Outside the eligibility limit, `waiting_for_window` is not offline.
- Last seen: last matching observation available to the current HA run.
- Delivery status: durable job status, including `physically_unverified`.
- Last refresh acknowledgement: latest acknowledged refresh across jobs,
  explicitly **not** proof of a correct physical image.
- Send diagnostic image: an explicit press checks the immutable diagnostic
  preview hash, then queues one job with a fifteen-minute expiry and the
  existing one-attempt/identity/GATT guards. Repeated presses cannot supersede
  an active job; rejection is atomic in the Bridge job store.

No send occurs on startup, discovery, polling or registration. This is not
arbitrary image upload, automatic rendering or firmware control. Stock overlay
limitations still apply. API failures make stock entities unavailable without
blocking existing receiver setup. An older Bridge missing this endpoint needs
upgrading before the page appears. Unload removes listeners and shuts down the
stock status coordinator. The preserved Bridge job store recreates the device
after restart; physical success still requires user inspection.

### Jobs queued after Bluetooth discovery

Home Assistant suppresses callbacks for unchanged advertisement payloads (see
the [Bluetooth API documentation](https://developers.home-assistant.io/docs/core/bluetooth/api/#clearing-cached-advertisement-history)).
The HA manager therefore checks its connectable discovery cache every five
seconds as well as handling discovery callbacks. It reads the latest observation
for each matching manufacturer, and only checks pending jobs when that
observation is within the eligibility limit (ten seconds in released 0.50.15;
five minutes in the candidate below). It rechecks after the pending-job HTTP
request and, in the candidate, again after claim before connecting. This uses HA's existing
scanners; it neither clears shared advertisement history nor starts a scanner.

Timer and discovery events share the same per-address in-flight guard. The
Bridge's atomic claim, expiry, exact identity and plan validation, GATT and MTU
checks, and single connection attempt remain unchanged. Unloading unregisters
the timer and callback and prevents unclaimed work from starting; an already
connected transfer is allowed to finish. Simulated tests cover late queueing
without another callback, overlapping triggers, stale/missing observations,
expiry, claim rejection and unload. These are not physical delivery evidence.

The fixed three-minute canary job can expire independently of the reported
five-minute power-on window. An expired job with zero attempts is not proof that
the radio or firmware failed. This change does not extend an authorized job's
expiry or automatically requeue it.

### Observation-age candidate (2026-09-11, not deployed)

The exact F6ED tag was visible through Blue BT Proxy at about -42 dBm with
cached observation ages of 37-43 seconds, while queued jobs expired with zero
attempts. The ten-second filter can reject those sightings; this is evidence
of an eligibility mismatch, not proof of the sole cause of failed delivery.

The candidate permits one existing authorized job attempt from a matching HA
connectable-cache observation aged 0-300 seconds. Older eligible observations
are labelled `recently_seen`, not `advertising`. Last seen retains the original
observation timestamp, including across repeated polls. Future, non-finite,
missing and over-limit observations are ineligible. Age and identity are
checked again after both HTTP awaits; expiration or unload after claim reports
failure without opening BLE or retrying.

This is a bounded cache-age heuristic, **not** a measured five-minute countdown
from battery connection: a sighting late in the real window can remain eligible
after the tag sleeps and consume the job's single attempt. It does not change
stock firmware, wake the tag, add retries, extend job expiry, or prove a clean
physical refresh. Deployment and a separately authorized power-on canary test
remain required. Other API/proxy failures remain possible contributors.

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

## Custom-image actions (Platform 0.50.9)

Custom images require Bridge and integration 0.50.9 or later and use the
existing HA BLE executor and durable queue, not a new scanner.
Only `DF:84:6B:DE:F6:ED` / `TRSEPD_F6ED` is admitted.

Prepare a single **128 x 296 PNG**, at most 128 KiB, as raw base64 (no data-URI
prefix). Remote URLs and server-local paths are not accepted. Animation and
other formats are rejected. Transparency is composited onto white, then pixels
are quantized deterministically to black/white/red without resizing or dithering.

Call `flexdisplay.preview_top52810_image` with `image_base64` and optional
`config_entry_id` (required when multiple Bridges are configured). Request its
action response. It returns `plan_sha256`, `logical_png_base64` and
`stock_png_base64`, without creating a job or performing BLE I/O. Decode the
two preview fields as PNGs and inspect the expected stock hatch bands.

```yaml
- action: flexdisplay.preview_top52810_image
  data:
    image_base64: "{{ png_base64 }}"
  response_variable: image_preview
```

After approving the preview, run the send separately with the same image bytes
and the returned hash:

```yaml
- action: flexdisplay.send_top52810_image
  data:
    image_base64: "{{ png_base64 }}"
    expected_plan_sha256: "<confirmed 64-character plan hash>"
  response_variable: image_job
```

The client rechecks the preview; the Bridge independently regenerates and
verifies the plan. Changed pixels or SID require new confirmation. The Bridge
preview/jobs APIs use `pattern: image` and `image_base64`. Raw image input is
not retained in the job; the existing durable frames still encode its pixels.
Custom images cannot replace active jobs. Each job allows one attempt within
900 seconds, and retains exact advertisement/GATT/MTU/ACK validation.

Follow the returned job ID using the delivery-status sensor or authenticated
job API. A refresh ACK is not physical verification. Inspect the screen.
This does not change firmware, clear busy state, alter disconnect timing or
repair device-registry linkage. Rollback is another separately approved image
write; stock overlays remain a limitation. No image is sent by setup or preview.

## Image upload and HA media files (Platform 0.50.10)

HA media-file actions require both Bridge and integration 0.50.10 or later.
In the Bridge web interface, open **Content**, choose the admitted F6ED tag,
choose a PNG/JPEG, choose **Fit** (white padding) or **Crop** (center crop),
and click **Preview image**. Compare the converted image with the predicted
stock-firmware appearance. Click **Send to F6ED** and confirm the overwrite.
Changing the file or layout invalidates the preview. A stale asynchronous
preview cannot enable Send. After sending, use **Check delivery** and inspect
the actual panel. If a send request fails, check HA delivery status before
retrying: a lost response may still mean the job was queued.

The existing HA preview/send actions accept exactly one of `image_file` or
`image_base64`. Use an absolute path such as `/media/dog.jpg` on the HA host,
not a Mac path or a URL. Files must be regular, non-symlink files beneath
/media, with no parent-directory traversal. Reads run in HA's executor:

```yaml
action: flexdisplay.preview_top52810_image
data:
  image_file: /media/dog.jpg
  resize_mode: fit
response_variable: preview
```

Run send separately with the same file/layout and the preview's `plan_sha256`
as `expected_plan_sha256`. The file is reread and reconverted; changed rendered
pixels fail hash confirmation. HA action users still handle the confirmation
hash; the upload interface handles it internally. Do not automatically chain
preview to send when human approval is required.

Both paths call the authenticated, offline
`POST /api/v1/stock-ble/top52810/images/prepare?resize_mode=fit` endpoint
with raw image bytes. It enforces 5 MiB while streaming and 12 million decoded
pixels, PNG/JPEG only and no animation. It honors EXIF orientation, composites
transparency on white, resizes and quantizes through the stock renderer.
Output contains a normalized native PNG and logical/stock previews and plan
hash. Source images are not stored in a library; queued frames still encode
their rendered pixels. Native 128 x 296 base64 input remains supported.

No changes to single-attempt delivery, active-job protection, fixed target,
firmware or disconnect timing. Live HA file-action and proxy delivery
verification remain post-deployment canary requirements.

## Friendly automation action and media picker

`flexdisplay.send_image` appears as **FlexDisplay: Send image to display** in
Home Assistant's **Perform action** picker. Select the experimental TOP52810
F6ED device, browse **My media** for a PNG/JPEG image, and choose Fit or Crop.
The media-picker enhancement is an implementation candidate, not yet deployed.
Upload the image to Home Assistant's local media library first; selecting media
does not upload a file from the user's Mac. Only local files under `/media` are
accepted (at most 5 MiB and 12 megapixels). URLs, streams, other media sources,
symlinks, and configured media directories outside `/media` are rejected.

```yaml
action: flexdisplay.send_image
data:
  device_id: YOUR_HOME_ASSISTANT_TAG_DEVICE_ID
  image_file:
    media_content_id: media-source://media_source/local/dog.png
    media_content_type: image/png
  resize_mode: fit
```

The picker supplies the media identifier; do not construct it manually when
using a differently named media directory. Home Assistant's media-source API
resolves it to a local path; the integration never downloads its playback URL.
Existing YAML using `image_file: /media/dog.png` remains supported, as do templates
that produce a local path.

The action reads the source once, converts through the existing bounded Bridge
renderer, and internally binds the resulting native image to its plan hash.
The existing client previews those exact bytes again and rejects changed plans
before queuing one job. No manual hash entry or separate preview step is needed.
Calling this action authorizes that image update; merely loading or configuring
an automation does not send anything. The optional response contains the queued
job, not proof of a completed physical refresh.

The selected device must be enabled and resolve unambiguously to the admitted
F6ED identity on its loaded Bridge. No fallback to another Bridge, generic
receiver, or other tag is permitted. Existing explicit preview/hash-send actions
remain unchanged. Active-job rejection, expiry, single-attempt BLE delivery,
and stock firmware limitations remain unchanged; this adds no automatic retry
or white clearing pass. Verify the picker and one separately authorized image
update after deployment before calling the feature physically validated.

## Response diagnostics candidate (2026-09-11)

The full-black job `top52810-00000019` received a refresh acknowledgement,
but the operator observed a partial update with a red tinge, not a black
screen. The subsequent white job `top52810-0000001a` stopped at session frame
1 with an unexpected notification. Version 0.50.13 did not retain those
notification bytes, so their value and the underlying cause are unknown.

The diagnostics candidate includes the expected acknowledgement, first eight
received bytes in hex, original response length and truncation flag in the
existing unexpected-notification error. This reaches the existing HA warning
and terminal job detail; it does not dump image frames or leases. Empty
responses are explicit. Transfer still stops immediately with notification
cleanup and no retry, changed timing or relaxed acknowledgement acceptance.
Deployment and a fresh single-device test are required to capture a new
response; this cannot recover bytes from the earlier failed session.

## Refresh-settle candidate (2026-09-11)

Job `top52810-0000001b` received the exact refresh-start acknowledgement but
the operator reported a whole-screen red cast and a faint dog image. Local
reconstruction matched both the stored render and plan hashes, with 31,485
white, 5,876 black and 527 red pixels; the red background was not requested.
The subsequent white job `top52810-0000001c` stopped at session start with
`30 35`, the stock command-03 busy response identified in the earlier firmware
analysis. No image data was sent by that white job.

The candidate retains the connection for 20 seconds after a successful
refresh acknowledgement, matching the earlier Mac sender, before disconnecting.
This is a fixed observation interval, not proof of panel completion. Errors
before refresh acceptance still disconnect immediately; busy is explicitly
reported as a failed single attempt, with no automatic retry or clearing pass.
Cancellation still releases the connection. No firmware or image encoding
changes are included. Immediate disconnect remains a hypothesis, not a proven
cause; deployment and a separately authorized physical test are required.

## Admission and implementation phases

1. **Architecture:** merge this ownership, identity, capability, transport,
   validation and recovery boundary. Family remains external/read-only.
2. **Portable identity experiment (complete for the canary):** identity remained
   stable across separate days, battery cycles and four ESPHome proxy sources
   on the intended Home Assistant controller.
3. **Pure codec tests (complete offline):** the transport-neutral codec now
   reproduces the physically verified 128 x 296 black/white/red plane hashes
   and complete 44-write plan hashes. It has no Bluetooth or device-I/O path
   and does not admit the family for upload.
4. **Compact rendering (complete offline):** the built-in Studio profile and
   native 128 x 296 renderer expose unsafe-region metadata, deterministic
   black/white/red output and the expected physical stock-overlay preview.
   Receiver and upload routes remain explicitly unavailable.
5. **One-tag transport canary:** implement Home Assistant-owned BLE delivery,
   durable Bridge job states, exact response checks and one named hardware
   canary. The command-gated adapter is implemented but remains unreleased and
   undeployed. Its read-only preview produces an immutable plan hash; a
   separate authenticated queue request must repeat that exact hash before the
   Home Assistant integration can claim it during the admitted tag's normal
   advertisement window. It performs one attempt, records
   `refresh_started`, and ends at `physically_unverified` until the panel is
   checked. The physical canary still requires a fresh device-write
   confirmation.
6. **Family admission:** update the released compatibility matrix only after
   the identity, security, renderer, transport, physical image and recovery
   gates pass. Firmware and administrative controls remain absent.
