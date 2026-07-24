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

The app exposes TCP port `8099` on the Home Assistant host.
