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
    "firmware_version": "1.5.0-flexdisplay.0.36.0",
    "firmware_url": (
        "https://github.com/clintonmarshall/xteink-flexdisplay/"
        "releases/download/v0.36.0/firmware.bin"
    ),
    "firmware_sha256": "b89eedbe13b67d54886d483add5f842556b1f72d98365519b161766b2b7e3202",
    "firmware_size": 5_881_056,
}
LEGACY_PACKAGED_FIRMWARE = (
    {
        "firmware_version": "1.5.0-flexdisplay.0.34.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay/"
            "releases/download/v0.34.0/firmware.bin"
        ),
        "firmware_sha256": "c1f14bbee86074ebe774e4d87344b8d7c5e10d0d74c88dda1a20c921dac7e035",
        "firmware_size": 5_877_248,
    },
    {
        "firmware_version": "1.5.0-flexdisplay.0.32.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay/"
            "releases/download/v0.32.0/firmware.bin"
        ),
        "firmware_sha256": "50514479cedbcf5261267c1a64500228514e2c58fc497be43f19a8c6d1ad3873",
        "firmware_size": 5_873_440,
    },
    {
        "firmware_version": "1.5.0-flexdisplay.0.31.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/firmware-v0.31.0/firmware.bin"
        ),
        "firmware_sha256": "d8694865dfc57e2d55efeca75d044d49703553f7cc2ad6c6f4a58c92d5897a38",
        "firmware_size": 5_873_152,
    },
    {
        "firmware_version": "1.4.1-flexdisplay.0.30.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/firmware-v0.30.0/firmware.bin"
        ),
        "firmware_sha256": "f7f108ba7a8035e287fc24163867ffa95403b873c0ac1c878b75bf95a9845b9d",
        "firmware_size": 5_575_968,
    },
    {
        "firmware_version": "1.4.1-flexdisplay.0.24.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/firmware-v0.24.0/firmware.bin"
        ),
        "firmware_sha256": "a913c956568d571014da928319623af84e64ca191a20a3a1e97c7c32c9a55e96",
        "firmware_size": 5_512_576,
    },
    {
        "firmware_version": "1.4.1-flexdisplay.0.23.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/firmware-v0.23.0/firmware.bin"
        ),
        "firmware_sha256": "cb16136e09512b2cb58ab51db6ff381afb2f98c41fdd45e97665650a67decc5f",
        "firmware_size": 5_512_016,
    },
    {
        "firmware_version": "1.4.1-flexdisplay.0.22.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/firmware-v0.22.0/firmware.bin"
        ),
        "firmware_sha256": "1ac6a8c057b4cf60109d679c06824f6b4507c98ae447a5cdf42db7c4f9a2149d",
        "firmware_size": 5_510_736,
    },
    {
        "firmware_version": "1.4.1-flexdisplay.0.21.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/firmware-v0.21.0/firmware.bin"
        ),
        "firmware_sha256": "06b09c2038777d27a01611f4c7d2fa95a2e07bf89a3360b597e036a7c18e6b2a",
        "firmware_size": 5_492_960,
    },
    {
        "firmware_version": "1.4.1-flexdisplay.0.19.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/firmware-v0.19.0/firmware.bin"
        ),
        "firmware_sha256": "812e07bfd9b7c0d67f1446609d2040b0ca876ba94c04ede76f7f290e072af3fb",
        "firmware_size": 5_489_488,
    },
    {
        "firmware_version": "1.4.1-flexdisplay.0.18.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/firmware-v0.18.0/firmware.bin"
        ),
        "firmware_sha256": "fc40c84a6106447fc3caf26bb373bf922fae5d3300f4aab5eb6cbcbe1a05cc90",
        "firmware_size": 5_487_184,
    },
    {
        "firmware_version": "1.4.1-flexdisplay.0.17.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/firmware-v0.17.0/firmware.bin"
        ),
        "firmware_sha256": "3f2912d4d2811442353ffba6fb2019167c6e0e600a04a0f176ffebae600a46ab",
        "firmware_size": 5_486_384,
    },
    {
        "firmware_version": "1.4.1-flexdisplay.0.14.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/firmware-v0.14.0/firmware.bin"
        ),
        "firmware_sha256": "f32000d6bb914b8e3bc923e62f7586e4b57a3bf4ddfa8e2e4c1e8d48793370b8",
        "firmware_size": 5_485_440,
    },
    {
        "firmware_version": "1.4.1-flexdisplay.0.13.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/v0.10.0/firmware.bin"
        ),
        "firmware_sha256": "900dcdf981579901deeb4913570cd4f5e7613d532b698b0ced03af45d47df214",
        "firmware_size": 5_483_808,
    },
)


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


def firmware_options(options: dict) -> dict[str, str]:
    """Migrate packaged or mixed-packaged manifests while preserving custom overrides."""
    packaged = (DEFAULT_FIRMWARE, *LEGACY_PACKAGED_FIRMWARE)
    if all(
        str(options.get(name, "")) in {
            "",
            "0" if name == "firmware_size" else "",
            *(str(release[name]) for release in packaged),
        }
        for name in DEFAULT_FIRMWARE
    ):
        return {name: str(value) for name, value in DEFAULT_FIRMWARE.items()}
    return {name: firmware_option(options, name) for name in DEFAULT_FIRMWARE}


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
    os.environ["FLEXDISPLAY_HA_ENTITY_SOURCE"] = option(
        options, "home_assistant_entity_source", "hacs"
    )
    os.environ["FLEXDISPLAY_FLEXHUB_URL"] = option(options, "flexhub_url")
    os.environ["FLEXDISPLAY_FLEXHUB_ACCESS_PIN"] = option(options, "flexhub_access_pin")
    os.environ["FLEXDISPLAY_FLEXHUB_POLL_SECONDS"] = option(
        options, "flexhub_poll_seconds", 15
    )
    os.environ["FLEXDISPLAY_SCREEN_HISTORY_ENABLED"] = option(
        options, "screen_history_enabled", True
    )
    os.environ["FLEXDISPLAY_SCREEN_HISTORY_LIMIT"] = option(
        options, "screen_history_limit", 5
    )
    os.environ["FLEXDISPLAY_BRIDGE_API_KEY"] = option(options, "bridge_api_key")
    firmware = firmware_options(options)
    os.environ["FLEXDISPLAY_FIRMWARE_VERSION"] = firmware["firmware_version"]
    os.environ["FLEXDISPLAY_FIRMWARE_URL"] = firmware["firmware_url"]
    os.environ["FLEXDISPLAY_FIRMWARE_SHA256"] = firmware["firmware_sha256"]
    os.environ["FLEXDISPLAY_FIRMWARE_SIZE"] = firmware["firmware_size"]
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
    os.environ["FLEXDISPLAY_FIRMWARE_RETRY_LIMIT"] = option(
        options, "firmware_retry_limit", 3
    )
    os.environ["FLEXDISPLAY_FIRMWARE_RETRY_BACKOFF_SECONDS"] = option(
        options, "firmware_retry_backoff_seconds", 300
    )
    os.environ["FLEXDISPLAY_FIRMWARE_MIRROR_ENABLED"] = option(
        options, "firmware_mirror_enabled", True
    )
    os.environ["FLEXDISPLAY_FIRMWARE_MIRROR_RETRY_SECONDS"] = option(
        options, "firmware_mirror_retry_seconds", 300
    )
    os.environ["FLEXDISPLAY_FIRMWARE_STALE_INSTALL_SECONDS"] = option(
        options, "firmware_stale_install_seconds", 1800
    )
    os.environ["FLEXDISPLAY_FIRMWARE_MAINTENANCE_ENABLED"] = option(
        options, "firmware_maintenance_enabled", False
    )
    os.environ["FLEXDISPLAY_FIRMWARE_MAINTENANCE_START"] = option(
        options, "firmware_maintenance_start", "01:00"
    )
    os.environ["FLEXDISPLAY_FIRMWARE_MAINTENANCE_END"] = option(
        options, "firmware_maintenance_end", "05:00"
    )
    os.environ["FLEXDISPLAY_FIRMWARE_MAINTENANCE_TIMEZONE"] = option(
        options, "firmware_maintenance_timezone", "Australia/Melbourne"
    )
    os.environ["FLEXDISPLAY_FIRMWARE_MAINTENANCE_USB_OVERRIDE"] = option(
        options, "firmware_maintenance_usb_override", True
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
