from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from flexdisplay_bridge.device_capabilities import (
    DEVICE_CAPABILITY_REGISTRY,
    resolve_device_capabilities,
)


@pytest.mark.parametrize(
    ("reported_model", "model_key", "provider", "dimensions"),
    [
        ("X3", "x3", "xteink", (528, 792)),
        ("XTEINK_X3", "x3", "xteink", (528, 792)),
        ("xteink-x4", "x4", "xteink", (480, 800)),
        ("X4_PRO", "x4_pro", "xteink", (480, 800)),
        ("N4", "note4", "note4", (400, 300)),
        ("ZECTRIX_NOTE4", "note4", "note4", (400, 300)),
        ("Echo Spot", "rook", "android_app", (480, 480)),
        ("AMAZON_ECHO_SPOT", "rook", "android_app", (480, 480)),
        ("CHECKERS", "checkers", "android_app", (960, 480)),
        ("Echo Show 5 2019", "checkers", "android_app", (960, 480)),
    ],
)
def test_current_model_aliases_resolve_to_trusted_descriptors(
    reported_model: str,
    model_key: str,
    provider: str,
    dimensions: tuple[int, int],
) -> None:
    descriptor = resolve_device_capabilities(reported_model)

    assert descriptor.model_key == model_key
    assert descriptor.known_model is True
    assert descriptor.firmware_provider == provider
    assert (descriptor.display.width, descriptor.display.height) == dimensions


def test_only_x3_and_x4_are_eligible_for_xteink_ota() -> None:
    eligible = {
        model_key
        for model_key, descriptor in DEVICE_CAPABILITY_REGISTRY.items()
        if descriptor.supports_xteink_ota
    }

    assert eligible == {"x3", "x4"}
    for model_key in eligible:
        descriptor = DEVICE_CAPABILITY_REGISTRY[model_key]
        assert descriptor.firmware.provider == "xteink"
        assert descriptor.firmware.manageable is True
        assert "install" in descriptor.actions


def test_x4_pro_identity_does_not_fall_back_to_legacy_x4_firmware() -> None:
    descriptor = resolve_device_capabilities("X4_PRO")

    assert descriptor.family == "xteink_x4_pro"
    assert descriptor.model_key == "x4_pro"
    assert descriptor.firmware.provider == "xteink"
    assert descriptor.firmware.artifact_family == "none"
    assert descriptor.firmware.manageable is False
    assert descriptor.supports_xteink_ota is False
    assert descriptor.management.actions == ()
    assert descriptor.hardware.management_profile == "read_only"
    assert descriptor.inputs.touch is None
    assert descriptor.frontlight.available is None

    # Hardware variants belong in dedicated identity headers. A suffixed model
    # must not be normalized back to either X4 or X4 Pro.
    suffixed = resolve_device_capabilities("X4_PRO_P4")
    assert suffixed.model_key == "unknown"
    assert suffixed.firmware.provider == "none"


def test_unverified_x4_pro_p4_report_stays_read_only_with_s3_claims() -> None:
    descriptor = resolve_device_capabilities(
        "X4_PRO",
        board_id="xteink_x4_pro",
        hardware_revision="p4",
        mcu_family="esp32-p4",
        flash_size_bytes=16 * 1024 * 1024,
        capabilities=(
            "touch,capacitive-home,side-buttons,frontlight,"
            "frontlight-brightness,frontlight-warmth"
        ),
    )

    assert descriptor.hardware.reported_identity_complete is False
    assert descriptor.hardware.management_profile == "read_only"
    assert descriptor.management.actions == ()
    assert descriptor.inputs.event_types == ()
    assert descriptor.frontlight.available is None
    assert descriptor.supports_xteink_ota is False


def test_x4_pro_s3_surfaces_reported_inputs_and_frontlight_without_ota() -> None:
    descriptor = resolve_device_capabilities(
        "X4_PRO",
        board_id="xteink_x4_pro",
        hardware_revision="s3",
        mcu_family="esp32-s3",
        flash_size_bytes=16 * 1024 * 1024,
        psram_size_bytes=8 * 1024 * 1024,
        capabilities=(
            "touch,capacitive-home,side-buttons,frontlight,"
            "frontlight-brightness,frontlight-warmth,sdmmc"
        ),
    )

    assert descriptor.hardware.management_profile == "s3"
    assert descriptor.inputs.touch is True
    assert descriptor.inputs.capacitive_buttons == ("home",)
    assert descriptor.inputs.physical_buttons == (
        "side_previous",
        "side_next",
        "power",
    )
    assert descriptor.inputs.event_types == (
        "home",
        "side_previous",
        "side_next",
        "power",
    )
    assert descriptor.management.supports_button_actions is False
    assert descriptor.frontlight.available is True
    assert descriptor.frontlight.supports_brightness is True
    assert descriptor.frontlight.supports_warmth is True
    assert descriptor.firmware.artifact_family == "x4pro_s3"
    assert descriptor.firmware.manageable is False
    assert descriptor.supports_xteink_ota is False
    assert "install" not in descriptor.management.actions


@pytest.mark.parametrize(
    "psram_size_bytes",
    [None, 0, 4 * 1024 * 1024, 16 * 1024 * 1024],
)
def test_x4_pro_s3_requires_exact_eight_mib_psram_for_capability_admission(
    psram_size_bytes: int | None,
) -> None:
    descriptor = resolve_device_capabilities(
        "X4_PRO",
        board_id="xteink_x4_pro",
        hardware_revision="s3",
        mcu_family="esp32-s3",
        flash_size_bytes=16 * 1024 * 1024,
        psram_size_bytes=psram_size_bytes,
        capabilities=(
            "touch,capacitive-home,side-buttons,frontlight,"
            "frontlight-brightness,frontlight-warmth,sdmmc"
        ),
    )

    assert descriptor.hardware.management_profile == "read_only"
    assert descriptor.management.actions == ()
    assert descriptor.inputs.event_types == ()
    assert descriptor.frontlight.available is None
    assert descriptor.firmware.artifact_family == "none"
    assert descriptor.supports_xteink_ota is False


def test_note4_uses_its_own_firmware_provider() -> None:
    descriptor = resolve_device_capabilities("NOTE4")

    assert descriptor.family == "note4_eink"
    assert descriptor.firmware.provider == "note4"
    assert descriptor.firmware.manageable is True
    assert descriptor.supports_xteink_ota is False
    assert "install" in descriptor.management.actions
    assert descriptor.delivery.supports_opendisplay is False


@pytest.mark.parametrize(
    ("model", "model_key", "shape"),
    [
        ("ROOK", "rook", "round"),
        ("Amazon Echo Show 5", "checkers", "rectangular"),
    ],
)
def test_android_receivers_expose_receiver_management_without_esp_ota(
    model: str,
    model_key: str,
    shape: str,
) -> None:
    descriptor = resolve_device_capabilities(model)

    assert descriptor.family == "android_receiver"
    assert descriptor.model_key == model_key
    assert descriptor.display.technology == "lcd"
    assert descriptor.display.color is True
    assert descriptor.display.shape == shape
    assert descriptor.power.power_class == "always_on_color"
    assert descriptor.power.reports_battery is False
    assert descriptor.power.reports_usb_power is False
    assert descriptor.delivery.refresh_delivery == "long_poll"
    assert descriptor.delivery.supports_long_poll is True
    assert descriptor.firmware.provider == "android_app"
    assert descriptor.firmware.manageable is False
    assert descriptor.supports_xteink_ota is False
    assert "install" not in descriptor.actions
    assert descriptor.management.supports_interactions is True
    assert descriptor.management.supports_notifications is True
    assert descriptor.management.supports_audio is True
    assert descriptor.management.supports_battery_policy is False
    assert descriptor.management.supports_sleep_policy is False


def test_arbitrary_esp_color_lcd_gets_mqtt_delivery_but_no_firmware_provider() -> None:
    descriptor = resolve_device_capabilities(
        "ESP32-S3-LCD",
        capabilities=(
            "color",
            "lcd",
            "always-on-color",
            "mqtt-screen-refresh",
            "touch",
        ),
        width=1024,
        height=600,
    )

    assert descriptor.family == "generic_embedded"
    assert descriptor.model_key == "generic_esp_lcd"
    assert descriptor.known_model is False
    assert descriptor.display.width == 1024
    assert descriptor.display.height == 600
    assert descriptor.display.technology == "lcd"
    assert descriptor.display.color is True
    assert descriptor.display.touch is True
    assert descriptor.power.power_class == "always_on_color"
    assert descriptor.power.battery_managed is False
    assert descriptor.delivery.refresh_delivery == "mqtt"
    assert descriptor.delivery.supports_mqtt_wake is True
    assert descriptor.firmware.provider == "none"
    assert descriptor.firmware.manageable is False
    assert descriptor.supports_xteink_ota is False
    assert descriptor.actions == ("refresh",)
    assert descriptor.management.supports_sleep_policy is False
    assert descriptor.management.supports_opendisplay_policy is False


def test_reported_capabilities_cannot_grant_a_trusted_family_or_ota() -> None:
    descriptor = resolve_device_capabilities(
        "ESP32-X4-LCD",
        capabilities="android,color,lcd,xteink,ota,mqtt_refresh",
    )

    assert descriptor.family == "generic_embedded"
    assert descriptor.firmware.provider == "none"
    assert descriptor.supports_xteink_ota is False
    assert "install" not in descriptor.actions
    assert descriptor.delivery.refresh_delivery == "mqtt"


def test_unrecognized_device_fails_closed() -> None:
    descriptor = resolve_device_capabilities(
        "Acme Mystery Panel",
        width=640,
        height=480,
    )

    assert descriptor.family == "unknown"
    assert descriptor.model_key == "unknown"
    assert descriptor.display.width == 640
    assert descriptor.display.height == 480
    assert descriptor.display.technology == "unknown"
    assert descriptor.firmware.provider == "none"
    assert descriptor.supports_xteink_ota is False
    assert descriptor.management.actions == ()
    assert descriptor.management.supports_provisioning is False
    assert descriptor.management.supports_fleet_policy is False


def test_color_capabilities_can_classify_a_generic_display_without_model_markers() -> None:
    descriptor = resolve_device_capabilities(
        "Kitchen Panel",
        capabilities="colour,tft,mains_powered,push-refresh-mqtt",
    )

    assert descriptor.family == "generic_embedded"
    assert descriptor.display.technology == "lcd"
    assert descriptor.display.color is True
    assert descriptor.power.power_class == "always_on_color"
    assert descriptor.delivery.refresh_delivery == "mqtt"
    assert descriptor.reported_capabilities == (
        "colour",
        "mains-powered",
        "push-refresh-mqtt",
        "tft",
    )


def test_descriptor_is_immutable_and_registry_cannot_be_modified() -> None:
    descriptor = resolve_device_capabilities("XTEINK_X3")

    with pytest.raises(FrozenInstanceError):
        descriptor.family = "unknown"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        descriptor.firmware.provider = "none"  # type: ignore[misc]
    with pytest.raises(TypeError):
        DEVICE_CAPABILITY_REGISTRY["unsafe"] = descriptor  # type: ignore[index]


def test_descriptor_serializes_to_json_compatible_management_contract() -> None:
    descriptor = resolve_device_capabilities(
        "Echo_Show_5",
        capabilities="android,color,touch,long-poll-refresh",
    )

    payload = descriptor.to_dict()
    assert payload["model_key"] == "checkers"
    assert payload["display"] == {
        "width": 960,
        "height": 480,
        "technology": "lcd",
        "color": True,
        "shape": "rectangular",
        "image_format": "png",
        "touch": True,
    }
    assert isinstance(payload["management"]["actions"], list)
    assert "install" not in payload["management"]["actions"]
    assert payload["firmware"] == {
        "provider": "android_app",
        "artifact_family": "android_app",
        "manageable": False,
        "supports_xteink_ota": False,
    }
    assert json.loads(descriptor.to_json()) == payload


@pytest.mark.parametrize(
    ("width", "height"),
    [(0, 480), (-1, 480), (True, 480), (480, 0)],
)
def test_invalid_dimension_overrides_are_rejected(
    width: int,
    height: int,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        resolve_device_capabilities("X4", width=width, height=height)
