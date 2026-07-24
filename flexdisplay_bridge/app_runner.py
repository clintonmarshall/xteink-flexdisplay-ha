"""Translate Home Assistant App options into bridge environment settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import uvicorn


OPTIONS_PATH = Path("/data/options.json")
CONFIG_PATH = Path("/config/config.yaml")


def option(options: dict, name: str, default: object = "") -> str:
    """Read one option as a string suitable for an environment variable."""
    value = options.get(name, default)
    if isinstance(value, bool):
        return "true" if value else "false"
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
