# FlexDisplay Bridge

This Home Assistant App runs the local image renderer and fleet API used by
FlexDisplay firmware on XTEINK X3 and X4 devices.

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

The app automatically uses Home Assistant's internal Supervisor API token. It
does not require a long-lived access token in its options.

FlexDisplay `0.17.0` adds per-device physical-button actions in Dashboard
Studio. Confirm and the direction buttons support short, double, and long
presses for dashboard navigation, entity controls, scenes, scripts,
automations, and validated Home Assistant services. Back and Power remain
reserved recovery controls.

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

The **Name card / ID pass** page template creates a large name-card tile plus a
QR tile. Edit the full name, role/title, organisation or ID, QR content, and
caption in Studio, then preview and assign it like any other dashboard
profile. A profile containing only fixed content does not require Home
Assistant entity access and displays `STANDALONE` in its status footer.
