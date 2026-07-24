# Configuration

The default configuration starts without MQTT and renders a bridge-status
screen until entity IDs are added to `config.yaml`.

Options:

- `dashboard_title`: heading rendered on each e-paper screen.
- `mqtt_enabled`: publish optional MQTT Discovery entities.
- `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password`: dedicated broker
  connection.
- `bridge_api_key`: protects bridge command endpoints and should also be
  entered in the HACS integration.
- `firmware_version`: latest FlexDisplay application version offered to devices.
- `firmware_url`: direct HTTP(S) URL for the application `firmware.bin`.
- `firmware_sha256`: lowercase SHA-256 digest for that exact file.
- `firmware_size`: exact firmware file size in bytes.
- `firmware_minimum_battery`: minimum charge percentage for an unplugged update.

The device downloads to SD, verifies the configured size and SHA-256 digest,
then uses the existing dual-partition firmware installer. Firmware `0.6.0`
must first be installed over USB before queued OTA commands are understood.

Bridge `0.3.0` accepts the SD-buffered physical-button events and extended
telemetry sent by firmware `0.7.0`. The HACS integration exposes these through
the Physical Button event entity and diagnostic sensors.

The app exposes TCP port `8099` on the Home Assistant host.
