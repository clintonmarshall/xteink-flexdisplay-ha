# FlexDisplay Bridge

This Home Assistant App runs the local image renderer and fleet API used by
FlexDisplay firmware on XTEINK X3 and X4 devices and by the Android receiver
for the original 2017 Echo Spot (`rook`).

Release-infrastructure changes are validated against the exact candidate as a
complete product release: Forgejo rebuilds this App and reruns the Bridge,
integration, Android, and aggregate gates even when the App source itself is
unchanged.

After installation:

1. Start the app.
2. Confirm `http://HOME_ASSISTANT_IP:8099/healthz` responds.
3. Power on a FlexDisplay 0.10.0 fleet device; it registers and receives its
   assignment automatically. Standard builds can still point
   `/.crosspoint/home-assistant.json` at
   `http://HOME_ASSISTANT_IP:8099/api/v1/screen`.
4. Either install the FlexDisplay custom integration through HACS, or enable
   MQTT and select the v0.20 **mqtt** entity source for an App-only
   installation.

## Echo Spot Android receiver

The companion project in `rook_receiver/` makes a LineageOS 18.1 Echo Spot a
first-class LAN display. It registers through the normal `/api/v1/screen`
endpoint as model `ROOK`, receives a 480 × 480 circular-safe colour render,
and reports Android, colour, touch, and round-screen capabilities. Dashboard
Studio includes a **Spot** preview target.

Publish the App's TCP port 8099 and configure the receiver with
`http://HOME_ASSISTANT_IP:8099`. Home Assistant then exposes the existing
availability, current-page, navigation, refresh, clear, sleep, restart, and
policy entities for the Spot. Android receivers never receive the X3/X4 OTA
image; the Bridge rejects firmware installation for this model.

Bridge 0.43 adds paired touch interactions and immediate notifications. Spot
tiles automatically control supported Home Assistant lights, switches,
input booleans, scenes, and covers. The `flexdisplay.notify` Home Assistant
action can deliver a camera snapshot, chime, expiry, and up to three bounded
action buttons to the receiver.

The app automatically uses Home Assistant's internal Supervisor API token. It
does not require a long-lived access token in its options.

FlexDisplay `0.17.0` adds per-device physical-button actions in Dashboard
Studio. Confirm and the direction buttons support short, double, and long
presses for dashboard navigation, entity controls, scenes, scripts,
automations, and validated Home Assistant services. Back and Power remain
reserved recovery controls.

Bridge `0.22.3` makes activation state explicit in Studio: mappings are saved
on the Bridge immediately and show **Ready** while the device is in Home
Assistant mode or **Waiting** when another device mode is active. An optional
on-screen legend can identify assigned buttons using a dotted line for short
press, double-dotted line for double press, and solid line for long press.
The legend is rendered above the status footer and does not cover dashboard
tiles.

FlexDisplay `0.18.0` adds a **Photo Frame** workspace to Dashboard Studio.
Create albums, upload JPEG/PNG/WebP/BMP images, import `camera.*` or `image.*`
entities, select crop/contain and rotation, add captions, preview the exact
1-bit X3/X4 output, and assign the album to a device. Album shuffle, rotation
intervals, timezone-aware active windows, and unchanged-image sleep handling
run in the Bridge so each device downloads only its final monochrome BMP.

FlexDisplay `0.19.0` adds recoverable fleet updates. Home Assistant shows
device-reported OTA stages, percentages, exact failures, and timestamps, with
guarded controls to cancel active commands, retry after bounded backoff, reset
a blocked rollout, and verify USB recovery. The Bridge verifies and mirrors the
configured release locally before giving its URL to devices; canary-first
gating remains enabled by default.

FlexDisplay Bridge `0.20.0` makes HACS optional. Its App-only MQTT mode creates
the complete Home Assistant device, including health, controls, configuration,
physical-button events, and firmware updates. It also adds a Fleet Health
workspace and a bounded history of exact rendered X3/X4 screens. Existing
HACS users remain on the duplicate-safe `hacs` entity source until they
explicitly migrate. Shared firmware `0.19.0` remains compatible.

FlexDisplay `0.21.0` adds **Branded Fetch Screens**. Dashboard Studio can
create a fleet-default or per-device loading screen with a logo, headline,
message, owner, device name, and area. The exact X3/X4 one-bit preview is
cached on the device SD card after its next successful check-in, so showing it
adds no network request to later dashboard fetches. Policies can show it on
every fetch, only after a manual wake, only on USB power, or never.

FlexDisplay `0.22.0` adds **Fleet Content** to Dashboard Studio. Upload a
validated content-pack ZIP, select one or many X3/X4 devices, and deploy
managed photos, books, logos, and other assets. Devices verify the manifest,
file sizes, and SHA-256 checksums before installing, then report pending,
installed, or failed at their next check-in. This is non-destructive and is
separate from the USB-only LDCS Factory Kit.

FlexDisplay Bridge `0.22.1` adds standalone Studio content. Create fixed text
tiles, encode text or URLs directly as QR codes, or apply the Name Card / ID
Pass template without selecting a Home Assistant entity. This is a Bridge-only
update; the shared X3/X4 firmware remains `0.22.0`.

FlexDisplay Bridge `0.22.2` expands standalone Studio content with a dedicated
QR Code Page, guided email, LinkedIn, website, contact, phone, Wi-Fi, and text
QR builders, profile-photo uploads, and four e-ink ID badge themes. This is
also a Bridge-only update; the shared X3/X4 firmware remains `0.22.0`.

FlexDisplay Bridge `0.22.3` adds independent per-tile **Text size** and
**QR code size** sliders. Text can be scaled from 60% to 180%, QR codes from
50% to 150%, and the live X3/X4 preview uses the exact saved layout. QR tiles
now reserve separate label, code, and caption regions so enlarged codes remain
readable without colliding with surrounding text. This is also Bridge-only.

FlexDisplay `0.23.0` reduces network and battery overhead. Upgraded devices
confirm that an image is physically cached before the Bridge returns an
unchanged response with no image body, and Photo Frame transfers use optimized
PNG instead of raw BMP. Earlier firmware continues receiving full images.
Home Assistant diagnostic sensors expose the last transfer format, size,
bytes saved, and percentage saved.

FlexDisplay `0.24.0` adds advanced fleet health. Shared X3/X4 firmware reports
a unique identifier for each boot and the ESP reset reason, including panic,
watchdog, brownout, deep-sleep, software, and power resets. The Bridge keeps a
bounded history and derives Wi-Fi trend, battery drain/runtime, missed
check-ins, repeated SD failures, and reset counters. Fleet Health visualizes
battery and Wi-Fi trends, and MQTT Discovery publishes matching diagnostic and
problem entities. Optional overnight firmware maintenance windows can gate
battery-powered rollouts while still allowing an explicit USB override.

The current fleet-controller development slice adds revisioned policy delivery
for the SenseCAP FlexHub and Home Assistant. Battery Saver, Balanced, and USB
Kiosk policies can target all displays, X3, X4, or an explicit device list.
The same request can preserve or change the default application, select a
Dashboard Studio profile, or assign a Photo Frame album. Each device reports
the last policy revision it has persisted, so the Bridge distinguishes
pending, synced, unmanaged, and mismatched state instead of treating a queued
refresh as proof that the change was applied.

The controller endpoints are:

- `GET /api/v1/fleet/policies` for profiles, available dashboards/albums, fleet
  health, and desired-versus-reported revision state.
- `PUT /api/v1/fleet/policy` for scoped policy, application, dashboard, and
  album assignment. This endpoint requires the configured Bridge API key.

For custom dashboard entity lists, edit `config.yaml` in the app's
`addon_configs` directory and restart the app.

## State-aware pages

Dashboard Studio can make a page part of the normal playlist, a scheduled page
set, or a priority alert. Scheduled sets use each device's configured timezone
and support overnight ranges such as `22:00` to `06:00`.

Alert pages watch a Home Assistant entity and can match exact text, numeric
thresholds, text fragments, on/off-style states, or unavailability. When
several alerts are active, the highest priority appears first. An optional
expiry is measured from the entity's `last_changed` time; after expiry the
normal or scheduled playlist resumes, and the alert rearms when the entity
changes again.

The Dashboard Studio includes starting templates for doorbells, alarms,
daytime energy, running appliances, weather alerts, and standalone ID passes.
Replace Home Assistant-based examples with entities from your system before
saving.

The **Warm household** template is purpose-built for the rectangular Android
Companion (1200 × 675) and Echo Show 5 (960 × 480). It renders a shared RGB
home illustration with garage and motion status, local date/time, indoor
temperature, humidity, site power, solar power, and battery charge. Its seven
tiles are a fixed semantic set rather than a generic grid; all other bitmap
layouts retain the four-tile limit. On a non-Android target the layout is not
offered as an LVGL page and is rejected by the LVGL delivery contract.

Image tiles can use a Home Assistant `camera.*` or `image.*` entity, or a
direct HTTP(S) URL reachable from the Bridge App. Choose **Crop to fill** for
edge-to-edge camera and artwork tiles, or **Fit whole image** when no part of
the source may be cropped. Studio previews the exact monochrome, dithered
result. Downloads are limited to 8 MB and 20 megapixels; direct URLs never
receive the Home Assistant API token.

## Standalone cards and QR codes

Dashboard Studio tiles can use **Fixed content (no HA entity)** as their data
source. Use **Text** for labels, instructions, room names, prices, notices, or
other fixed values. Use **QR code** to encode a URL or arbitrary text directly;
the encoded value is not printed below the code unless it is separately added
as a caption.

The **QR code page** template creates a full-page QR display. Choose a website,
LinkedIn profile, plain text, Wi-Fi network, contact card, email message, or
phone number and Studio builds the standard QR payload from friendly fields.
The original text editor remains available through the plain-text type for
custom payloads.

The **Name card / ID pass** page template creates a large name-card tile plus a
QR tile. Edit the full name, role/title, organisation or ID, upload a profile
picture, and choose the Classic, Bold Band, Diagonal, or Halftone badge theme.
The QR tile can open an email draft, LinkedIn profile, website, contact card,
phone number, Wi-Fi setup, or custom text. Uploaded JPEG, PNG, WebP, and BMP
photos are normalized into Bridge-managed PNG assets (maximum 5 MB), then
cropped and dithered specifically for the X3/X4 e-paper display. Preview and
assign the finished pass like any other dashboard profile. A profile
containing only fixed content does not require Home Assistant entity access
and displays `STANDALONE` in its status footer.

## Colour and LVGL displays

Dashboard Studio includes a built-in **JC3636W518EN** target with a 360 × 360
round colour preview and a starter profile containing receiver-supported Home
Assistant status, gauge/progress, and touch-control pages. LVGL profiles are
declarative and bounded to 12 pages and four tiles per page. Receiver contract
v1 supports value cards, gauge/progress bars, button/toggle affordances,
approved themes, semantic colour roles, and tap actions. House Pulse layouts,
dashboard icons, QR, history, image, name-card/profile-photo, badge-theme, and
text/QR scale controls remain e-paper features until a receiver version
implements them. LVGL v1 uses receiver-native widget affordances and omits the
unused icon field from its manifest. Studio hides those choices for LVGL v1,
and both preview and device delivery reject an incompatible saved profile
instead of silently rendering a different screen. The contract does not accept
free-form coordinates, executable callbacks, firmware pins, or source code.

Use **Display hardware → Open Profile Builder** to add another RGB565 or
RGB888 colour/LVGL panel. A custom hardware profile records display metadata,
dimensions, shape, touch/controller capability, MCU, memory, and model aliases.
It does not generate board firmware or prove that a pin mapping is compatible.
Built-in profiles are read-only, and profiles already assigned to a device are
protected from incompatible edits or deletion by the Bridge API.

LVGL-only colour and touch fields remain hidden for X3/X4 e-paper and Android
receiver previews, while e-paper-only visual and scaling fields remain hidden
for LVGL v1. Home Assistant actions are saved as validated declarative data and
resolved by the Bridge; Home Assistant credentials are never included in the
receiver manifest.

Colour receivers use device-bound authentication. Configure a distinct
Bridge-only `lvgl_receiver_key_master` containing 16–256 UTF-8 bytes; never
copy that master onto a panel. The provisioning workflow derives one lowercase
64-character HMAC-SHA256 key using the domain separator
`flexdisplay.lvgl-receiver-key.v1` plus a NUL byte and the receiver's canonical
uppercase device ID. Credential rotations insert ASCII `epoch:<decimal>` plus
a second NUL byte after the v1 domain and before the device ID. Provision only
the derived key; the Bridge
API never returns the master or a derived value. JC3636 identities use `JC36-`
plus all 12 uppercase hexadecimal MAC digits, for example
`JC36-001122AABBCC`.

The Home Assistant App stores the master as a password option, then hands it to
the Bridge through an owner-only fixed-purpose file rather than an environment
variable or YAML literal. Receiver credentials remain separate from the Bridge
administrator key, Home Assistant, Wi-Fi, ESPHome API encryption, and OTA
credentials. An administrator can revoke a lost receiver or advance its epoch
through the authenticated `/api/v1/receiver-credentials/{device_id}/revoke`
and `/rotate` routes; rotation requires provisioning the newly derived key on
that panel before it can reconnect.

The derived key is a bearer credential, so receivers must use verified HTTPS
for manifests, assets, and events. Event bindings are frozen with the delivered
manifest and each event is durably recorded before its side effect. Exact
duplicates return the stored terminal result; pending or failed events are not
re-executed. This is deliberate durable at-most-once behavior, not exactly-once
delivery.
