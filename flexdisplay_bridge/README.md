# FlexDisplay Bridge

This Home Assistant App runs the local image renderer and fleet API used by
FlexDisplay firmware on XTEINK X3 and X4 devices.

After installation:

1. Start the app.
2. Confirm `http://HOME_ASSISTANT_IP:8099/healthz` responds.
3. Point each device's `/.crosspoint/home-assistant.json` at
   `http://HOME_ASSISTANT_IP:8099/api/v1/screen`.
4. Install the FlexDisplay custom integration through HACS and enter the same
   bridge URL.

The app automatically uses Home Assistant's internal Supervisor API token. It
does not require a long-lived access token in its options.

For custom dashboard entity lists, edit `config.yaml` in the app's
`addon_configs` directory and restart the app.
