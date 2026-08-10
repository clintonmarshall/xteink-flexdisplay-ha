import hashlib
from pathlib import Path

from app_runner import (
    DEFAULT_FIRMWARE,
    DEFAULT_NOTE4_FIRMWARE,
    LEGACY_PACKAGED_FIRMWARE,
    firmware_option,
    firmware_options,
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
