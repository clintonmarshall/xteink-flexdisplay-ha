"""Translate Home Assistant App options into bridge environment settings."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from urllib.request import Request, urlopen

import uvicorn

OPTIONS_PATH = Path("/data/options.json")
CONFIG_PATH = Path("/config/config.yaml")
LVGL_RECEIVER_MASTER_PATH = Path("/data/flexdisplay-lvgl-receiver-master")
DEFAULT_FIRMWARE = {
    "firmware_version": "1.5.0-flexdisplay.0.39.0",
    "firmware_url": "packaged",
    "firmware_sha256": "eb9a788cdbbcd16a1c51cf19d1a42894a0975bf387976bd2a1c8d8c604820dd7",
    "firmware_size": 5_976_336,
}
DEFAULT_NOTE4_FIRMWARE = {
    "note4_firmware_version": "1.2.2-voice-remote",
    "note4_firmware_url": "packaged",
    "note4_firmware_sha256": (
        "1619c9788c050038d28e0f927b19d830ce7de18694ae1b407a639bcbd013ef18"
    ),
    "note4_firmware_size": 2_730_112,
}
LEGACY_PACKAGED_FIRMWARE = (
    {
        "firmware_version": "1.5.0-flexdisplay.0.38.1",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/v0.38.1/firmware.bin"
        ),
        "firmware_sha256": "068060b3780267d51ba7c8ea3de08da5c773361fda01b10641a9ecf35c264724",
        "firmware_size": 5_967_968,
    },
    {
        "firmware_version": "1.5.0-flexdisplay.0.37.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay-ha/"
            "releases/download/v0.37.0/firmware.bin"
        ),
        "firmware_sha256": "6c1d7e028e45f1c7e26dd80539e7207b1bbc4bca485d8104da250391f83126d5",
        "firmware_size": 5_917_888,
    },
    {
        "firmware_version": "1.5.0-flexdisplay.0.36.0",
        "firmware_url": (
            "https://github.com/clintonmarshall/xteink-flexdisplay/"
            "releases/download/v0.36.0/firmware.bin"
        ),
        "firmware_sha256": "039a2d04325a8dadab911322abf10e4a2b098ac8b95af7662a015c4db15d98eb",
        "firmware_size": 5_909_328,
    },
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


def note4_firmware_options(options: dict) -> dict[str, str]:
    """Backfill Note4 release metadata for existing App installations."""
    resolved: dict[str, str] = {}
    for name, default in DEFAULT_NOTE4_FIRMWARE.items():
        value = options.get(name)
        if value is None or value == "" or (
            name == "note4_firmware_size" and int(value) <= 0
        ):
            value = default
        resolved[name] = str(value)
    return resolved


def supervisor_mqtt_service(token: str) -> dict[str, object]:
    """Read the MQTT service credentials exposed to this Home Assistant App."""
    if not token:
        return {}
    request = Request(
        "http://supervisor/services/mqtt",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    return data if isinstance(data, dict) else {}


def mqtt_options(
    options: dict,
    supervisor_token: str,
    service_reader=supervisor_mqtt_service,
) -> dict[str, str]:
    """Resolve fresh-install MQTT defaults without requiring the HACS integration."""
    enabled = bool(options.get("mqtt_enabled", True))
    host = str(options.get("mqtt_host") or "").strip()
    port = int(options.get("mqtt_port") or 1883)
    username = str(options.get("mqtt_username") or "")
    password = str(options.get("mqtt_password") or "")

    if enabled and not host:
        service = service_reader(supervisor_token)
        host = str(service.get("host") or "").strip()
        port = int(service.get("port") or port)
        username = str(service.get("username") or username)
        password = str(service.get("password") or password)

    return {
        "enabled": "true" if enabled else "false",
        "host": host or "core-mosquitto",
        "port": str(port),
        "username": username,
        "password": password,
        "entity_source": str(
            options.get("home_assistant_entity_source") or "mqtt"
        ),
    }


def main() -> None:
    """Configure and launch the bridge."""
    options = json.loads(OPTIONS_PATH.read_text(encoding="utf-8")) if OPTIONS_PATH.exists() else {}
    supervisor_token = os.getenv("SUPERVISOR_TOKEN", "")
    os.environ["FLEXDISPLAY_HA_TOKEN"] = supervisor_token
    os.environ["FLEXDISPLAY_DASHBOARD_TITLE"] = option(options, "dashboard_title", "HOME ASSISTANT")
    mqtt = mqtt_options(options, supervisor_token)
    os.environ["FLEXDISPLAY_MQTT_ENABLED"] = mqtt["enabled"]
    os.environ["FLEXDISPLAY_MQTT_HOST"] = mqtt["host"]
    os.environ["FLEXDISPLAY_MQTT_PORT"] = mqtt["port"]
    os.environ["FLEXDISPLAY_MQTT_USERNAME"] = mqtt["username"]
    os.environ["FLEXDISPLAY_MQTT_PASSWORD"] = mqtt["password"]
    os.environ["FLEXDISPLAY_HA_ENTITY_SOURCE"] = mqtt["entity_source"]
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
    receiver_master = option(options, "lvgl_receiver_key_master")
    if receiver_master:
        encoded_master = receiver_master.encode("utf-8", errors="strict")
        if (
            not 16 <= len(encoded_master) <= 256
            or any(character < " " or character == "\x7f" for character in receiver_master)
        ):
            raise ValueError(
                "LVGL receiver key master must contain 16-256 UTF-8 bytes without control characters"
            )
        LVGL_RECEIVER_MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_master = LVGL_RECEIVER_MASTER_PATH.with_suffix(".tmp")
        try:
            descriptor = os.open(
                temporary_master,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_TRUNC
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                os.close(descriptor)
                raise OSError("LVGL receiver master temporary path is unsafe")
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(receiver_master)
                output.flush()
                os.fsync(output.fileno())
            temporary_master.replace(LVGL_RECEIVER_MASTER_PATH)
            LVGL_RECEIVER_MASTER_PATH.chmod(0o600)
            directory = os.open(LVGL_RECEIVER_MASTER_PATH.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            try:
                temporary_master.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    elif LVGL_RECEIVER_MASTER_PATH.is_symlink():
        raise ValueError("LVGL receiver master path must not be a symlink")
    elif LVGL_RECEIVER_MASTER_PATH.exists():
        # Do not silently keep accepting a master after the protected App
        # option has been explicitly cleared.
        LVGL_RECEIVER_MASTER_PATH.unlink()
        directory = os.open(LVGL_RECEIVER_MASTER_PATH.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    os.environ["FLEXDISPLAY_FIRMWARE_CONFIGURED_VERSION"] = option(
        options, "firmware_version"
    )
    firmware = firmware_options(options)
    os.environ["FLEXDISPLAY_FIRMWARE_CONFIG_SOURCE"] = (
        "packaged_release"
        if firmware["firmware_url"] == "packaged"
        else "home_assistant_app"
    )
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
    os.environ["FLEXDISPLAY_NOTE4_FIRMWARE_CONFIGURED_VERSION"] = option(
        options, "note4_firmware_version"
    )
    note4_firmware = note4_firmware_options(options)
    os.environ["FLEXDISPLAY_NOTE4_FIRMWARE_CONFIG_SOURCE"] = (
        "packaged_release"
        if note4_firmware["note4_firmware_url"] == "packaged"
        else "home_assistant_app"
    )
    os.environ["FLEXDISPLAY_NOTE4_FIRMWARE_VERSION"] = note4_firmware[
        "note4_firmware_version"
    ]
    os.environ["FLEXDISPLAY_NOTE4_FIRMWARE_URL"] = note4_firmware[
        "note4_firmware_url"
    ]
    os.environ["FLEXDISPLAY_NOTE4_FIRMWARE_SHA256"] = note4_firmware[
        "note4_firmware_sha256"
    ]
    os.environ["FLEXDISPLAY_NOTE4_FIRMWARE_SIZE"] = note4_firmware[
        "note4_firmware_size"
    ]
    os.environ["FLEXDISPLAY_NOTE4_FIRMWARE_MINIMUM_BATTERY"] = option(
        options, "note4_firmware_minimum_battery", 40
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
