import hashlib
import json
from pathlib import Path

from app_runner import (
    DEFAULT_FIRMWARE,
    DEFAULT_NOTE4_FIRMWARE,
    LEGACY_PACKAGED_FIRMWARE,
    firmware_option,
    firmware_options,
    mqtt_options,
    note4_firmware_options,
)


def test_bundled_firmware_matches_default_manifest() -> None:
    firmware_path = Path(__file__).resolve().parents[1] / "firmware" / "firmware.bin"
    payload = firmware_path.read_bytes()

    assert len(payload) == DEFAULT_FIRMWARE["firmware_size"]
    assert hashlib.sha256(payload).hexdigest() == DEFAULT_FIRMWARE["firmware_sha256"]


def test_bundled_note4_firmware_matches_default_manifest() -> None:
    firmware_path = Path(__file__).resolve().parents[1] / "firmware" / "note4.bin"
    payload = firmware_path.read_bytes()

    assert len(payload) == DEFAULT_NOTE4_FIRMWARE["note4_firmware_size"]
    assert (
        hashlib.sha256(payload).hexdigest()
        == DEFAULT_NOTE4_FIRMWARE["note4_firmware_sha256"]
    )


def test_note4_firmware_options_backfills_existing_install() -> None:
    options = {
        "note4_firmware_version": "",
        "note4_firmware_url": "",
        "note4_firmware_sha256": "",
        "note4_firmware_size": 0,
    }

    assert note4_firmware_options(options) == {
        name: str(value) for name, value in DEFAULT_NOTE4_FIRMWARE.items()
    }


def test_note4_firmware_options_preserves_custom_manifest() -> None:
    options = {
        "note4_firmware_version": "9.9.9",
        "note4_firmware_url": "https://example.test/note4.bin",
        "note4_firmware_sha256": "b" * 64,
        "note4_firmware_size": 4321,
    }

    assert note4_firmware_options(options) == {
        name: str(value) for name, value in options.items()
    }


def test_firmware_option_uses_packaged_release_for_missing_values() -> None:
    options = {
        "firmware_version": "",
        "firmware_url": "",
        "firmware_sha256": "",
        "firmware_size": 0,
    }

    for name, expected in DEFAULT_FIRMWARE.items():
        assert firmware_option(options, name) == str(expected)


def test_firmware_option_preserves_explicit_override() -> None:
    options = {
        "firmware_version": "9.9.9",
        "firmware_url": "https://example.test/firmware.bin",
        "firmware_sha256": "a" * 64,
        "firmware_size": 1234,
    }

    assert firmware_option(options, "firmware_version") == "9.9.9"
    assert firmware_option(options, "firmware_url") == "https://example.test/firmware.bin"
    assert firmware_option(options, "firmware_sha256") == "a" * 64
    assert firmware_option(options, "firmware_size") == "1234"


def test_firmware_options_migrates_exact_packaged_release() -> None:
    options = dict(LEGACY_PACKAGED_FIRMWARE[0])

    assert firmware_options(options) == {
        name: str(value) for name, value in DEFAULT_FIRMWARE.items()
    }


def test_firmware_options_migrates_older_saved_app_options() -> None:
    options = next(
        dict(release)
        for release in LEGACY_PACKAGED_FIRMWARE
        if release["firmware_version"] == "1.4.1-flexdisplay.0.24.0"
    )

    assert firmware_options(options) == {
        name: str(value) for name, value in DEFAULT_FIRMWARE.items()
    }


def test_firmware_options_repairs_mixed_packaged_release_metadata() -> None:
    options = dict(LEGACY_PACKAGED_FIRMWARE[0])
    options["firmware_version"] = LEGACY_PACKAGED_FIRMWARE[1]["firmware_version"]

    assert firmware_options(options) == {
        name: str(value) for name, value in DEFAULT_FIRMWARE.items()
    }


def test_firmware_options_preserves_custom_manifest() -> None:
    options = dict(LEGACY_PACKAGED_FIRMWARE[0])
    options["firmware_url"] = "https://example.test/custom.bin"

    resolved = firmware_options(options)

    assert resolved["firmware_version"] == options["firmware_version"]
    assert resolved["firmware_url"] == options["firmware_url"]
    assert resolved["firmware_sha256"] == options["firmware_sha256"]
    assert resolved["firmware_size"] == str(options["firmware_size"])


def test_mqtt_options_discovers_supervisor_service_for_fresh_install() -> None:
    def service_reader(token: str) -> dict[str, object]:
        assert token == "supervisor-token"
        return {
            "host": "172.30.33.2",
            "port": "1883",
            "username": "bridge-user",
            "password": "bridge-password",
        }

    assert mqtt_options({}, "supervisor-token", service_reader) == {
        "enabled": "true",
        "host": "172.30.33.2",
        "port": "1883",
        "username": "bridge-user",
        "password": "bridge-password",
        "entity_source": "mqtt",
    }


def test_mqtt_options_preserves_explicit_hacs_and_broker_settings() -> None:
    called = False

    def service_reader(_token: str) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    resolved = mqtt_options(
        {
            "mqtt_enabled": False,
            "mqtt_host": "mqtt.example.test",
            "mqtt_port": 2883,
            "mqtt_username": "custom-user",
            "mqtt_password": "custom-password",
            "home_assistant_entity_source": "hacs",
        },
        "supervisor-token",
        service_reader,
    )

    assert called is False
    assert resolved == {
        "enabled": "false",
        "host": "mqtt.example.test",
        "port": "2883",
        "username": "custom-user",
        "password": "custom-password",
        "entity_source": "hacs",
    }


def test_main_records_configured_and_effective_firmware_sources(
    tmp_path: Path, monkeypatch
) -> None:
    import app_runner

    legacy = next(
        dict(release)
        for release in LEGACY_PACKAGED_FIRMWARE
        if release["firmware_version"] == "1.4.1-flexdisplay.0.24.0"
    )
    options_path = tmp_path / "options.json"
    options_path.write_text(json.dumps(legacy), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("dashboard: {}\n", encoding="utf-8")
    monkeypatch.setattr(app_runner, "OPTIONS_PATH", options_path)
    monkeypatch.setattr(app_runner, "CONFIG_PATH", config_path)
    monkeypatch.setattr(app_runner.uvicorn, "run", lambda *args, **kwargs: None)
    environment: dict[str, str] = {}
    monkeypatch.setattr(app_runner.os, "environ", environment)

    app_runner.main()

    assert environment["FLEXDISPLAY_FIRMWARE_CONFIGURED_VERSION"] == (
        legacy["firmware_version"]
    )
    assert environment["FLEXDISPLAY_FIRMWARE_VERSION"] == str(
        DEFAULT_FIRMWARE["firmware_version"]
    )
    assert environment["FLEXDISPLAY_FIRMWARE_CONFIG_SOURCE"] == (
        "packaged_release"
    )
