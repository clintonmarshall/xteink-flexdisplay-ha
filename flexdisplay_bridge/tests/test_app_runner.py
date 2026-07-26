from app_runner import (
    DEFAULT_FIRMWARE,
    LEGACY_PACKAGED_FIRMWARE,
    firmware_option,
    firmware_options,
)


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


def test_firmware_options_preserves_custom_manifest() -> None:
    options = dict(LEGACY_PACKAGED_FIRMWARE[0])
    options["firmware_url"] = "https://example.test/custom.bin"

    resolved = firmware_options(options)

    assert resolved["firmware_version"] == options["firmware_version"]
    assert resolved["firmware_url"] == options["firmware_url"]
    assert resolved["firmware_sha256"] == options["firmware_sha256"]
    assert resolved["firmware_size"] == str(options["firmware_size"])
