"""Translate Home Assistant App options into bridge environment settings."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import uvicorn

OPTIONS_PATH = Path("/data/options.json")
CONFIG_PATH = Path("/config/config.yaml")
DEFAULT_FIRMWARE = {
    "firmware_version": "1.4.1-flexdisplay.0.13.0",
    "firmware_url": (
        "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
        "releases/download/v0.10.0/firmware.bin"
    ),
    "firmware_sha256": "900dcdf981579901deeb4913570cd4f5e7613d532b698b0ced03af45d47df214",
    "firmware_size": 5_483_808,
}


def option(options: dict, name: str, default: object = "") -> str:
    """Read one option as a string suitable for an environment variable."""
    value = options.get(name, default)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def firmware_option(options: dict, name: str) -> str:
    """Use the packaged release when an existing install has a blank option."""
    value = options.get(name)
    if value is None or value == "" or (name == "firmware_size" and int(value) <= 0):
        value = DEFAULT_FIRMWARE[name]
    return str(value)


def main() -> None:
    """Configure and launch the bridge."""
    options = json.loads(OPTIONS_PATH.read_text(encoding="utf-8")) if OPTIONS_PATH.exists() else {}
    os.environ["FLEXDISPLAY_HA_TOKEN"] = os.getenv("SUPERVISOR_TOKEN", "")
    os.environ["FLEXDISPLAY_DASHBOARD_TITLE"] = option(options, "dashboard_title", "HOME ASSISTANT")
    os.environ["FLEXDISPLAY_MQTT_ENABLED"] = option(options, "mqtt_enabled", False)
    os.environ["FLEXDISPLAY_MQTT_HOST"] = option(options, "mqtt_host", "core-mosquitto")
    os.environ["FLEXDISPLAY_MQTT_PORT"] = option(options, "mqtt_port", 1883)
    os.environ["FLEXDISPLAY_MQTT_USERNAME"] = option(options, "mqtt_username", "flexdisplay")
    os.environ["FLEXDISPLAY_MQTT_PASSWORD"] = option(options, "mqtt_password")
    os.environ["FLEXDISPLAY_BRIDGE_API_KEY"] = option(options, "bridge_api_key")
    os.environ["FLEXDISPLAY_FIRMWARE_VERSION"] = firmware_option(options, "firmware_version")
    os.environ["FLEXDISPLAY_FIRMWARE_URL"] = firmware_option(options, "firmware_url")
    os.environ["FLEXDISPLAY_FIRMWARE_SHA256"] = firmware_option(options, "firmware_sha256")
    os.environ["FLEXDISPLAY_FIRMWARE_SIZE"] = firmware_option(options, "firmware_size")
    os.environ["FLEXDISPLAY_FIRMWARE_MINIMUM_BATTERY"] = option(
        options, "firmware_minimum_battery", 40
    )
    os.environ["FLEXDISPLAY_FIRMWARE_CANARY_REQUIRED"] = option(
        options, "firmware_canary_required", True
    )
    os.environ["FLEXDISPLAY_FIRMWARE_REQUIRE_USB_FOR_CANARY"] = option(
        options, "firmware_require_usb_for_canary", True
    )
    os.environ["FLEXDISPLAY_FIRMWARE_MAX_PARALLEL"] = option(
        options, "firmware_max_parallel", 1
    )

    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile("/app/config.example.yaml", CONFIG_PATH)

    uvicorn.run(
        "flexdisplay_bridge.app:app",
        host="0.0.0.0",
        port=8099,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
