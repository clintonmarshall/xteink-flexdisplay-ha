from app_runner import DEFAULT_FIRMWARE, firmware_option


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
