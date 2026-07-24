# XTEINK FlexDisplay for Home Assistant

Public Home Assistant installer for FlexDisplay-enabled XTEINK X3 and X4
e-paper devices.

This repository provides:

- the **FlexDisplay Bridge** Home Assistant App, which renders device-sized
  dashboard images and records fleet telemetry;
- the **FlexDisplay** HACS integration, which creates Home Assistant devices,
  sensors, and refresh buttons.

It intentionally contains no device firmware, factory backups, credentials,
device identities, or private network configuration.

## 1. Install the Bridge App

1. In Home Assistant, open **Settings → Apps → App store**.
2. Open the repository menu and add:

   ```text
   https://github.com/clintonmarshall/xteink-flexdisplay-ha
   ```

3. Install **FlexDisplay Bridge**.
4. Start it and enable automatic startup.
5. Open `http://HOME_ASSISTANT_IP:8099/healthz` and confirm that it reports
   `"status": "ok"`.

The App uses Home Assistant's internal API token. No long-lived Home Assistant
token is required in the App options.

## 2. Install the HACS integration

1. Open **HACS → Integrations**.
2. Open **Custom repositories**.
3. Add:

   ```text
   https://github.com/clintonmarshall/xteink-flexdisplay-ha
   ```

   Select category **Integration**.

4. Install **FlexDisplay** and restart Home Assistant.
5. Open **Settings → Devices & services → Add integration → FlexDisplay**.
6. Enter `http://HOME_ASSISTANT_IP:8099`.
7. If the Bridge App has an API key, enter the same key.

## 3. Configure each X3/X4

Place this file on the device SD card at:

```text
/.crosspoint/home-assistant.json
```

```json
{
  "snapshot_url": "http://HOME_ASSISTANT_IP:8099/api/v1/screen",
  "auth_token_obf": "",
  "refresh_interval_seconds": 300,
  "live_mode": true
}
```

Use `live_mode: true` while testing or while USB-powered. Use `false` for a
battery-safe one-shot update. A sleeping ESP32 cannot receive an immediate
network command; queued commands run at the next device check-in.

## Entities

For each device known to the bridge, the integration exposes:

- battery level;
- Wi-Fi signal;
- last check-in;
- firmware version;
- active FlexDisplay mode;
- refresh button.

Devices first discovered after integration setup may require a FlexDisplay
integration reload before their entities appear.

## Security

Keep port 8099 on a trusted LAN. Configure a Bridge API key before exposing
command endpoints to other networks. Never commit Home Assistant tokens, MQTT
passwords, Wi-Fi credentials, device SD-card contents, or flash backups.

## License

GPL-3.0. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
