"""Contract tests for capability filtering in the Home Assistant integration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_capability_helpers() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "custom_components"
        / "flexdisplay"
        / "device_capabilities.py"
    )
    spec = importlib.util.spec_from_file_location("ha_device_capabilities", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPABILITIES = _load_capability_helpers()


def test_legacy_firmware_fallback_is_an_exact_allowlist() -> None:
    expected = {
        "XTEINK_X4": ("xteink", True, True),
        "ZECTRIX_NOTE4": ("note4", True, False),
        "ROOK": ("android_app", False, False),
        "CHECKERS": ("android_app", False, False),
        "ESP32-S3-LCD": ("none", False, False),
        "mystery-display": ("none", False, False),
    }

    for model, contract in expected.items():
        record = {"model": model}
        assert (
            CAPABILITIES.firmware_provider(record),
            CAPABILITIES.firmware_manageable(record),
            CAPABILITIES.supports_xteink_ota(record),
        ) == contract


def test_bridge_descriptor_overrides_legacy_model_inference() -> None:
    record = {
        "model": "XTEINK_X4",
        "supported_actions": [],
        "device_capabilities": {
            "firmware": {
                "provider": "none",
                "manageable": False,
                "supports_xteink_ota": False,
            },
            "power": {"reports_battery": False, "reports_usb_power": False},
        },
    }

    assert CAPABILITIES.supported_actions(record) == frozenset()
    assert CAPABILITIES.firmware_provider(record) == "none"
    assert CAPABILITIES.firmware_manageable(record) is False
    assert CAPABILITIES.supports_xteink_ota(record) is False
    assert CAPABILITIES.reports_battery(record) is False
    assert CAPABILITIES.reports_usb_power(record) is False


def test_legacy_actions_fail_closed_for_unknown_devices() -> None:
    assert CAPABILITIES.supported_actions({"model": "XTEINK_X3"}) == {
        "refresh",
        "full-refresh",
        "next",
        "previous",
        "overview",
        "clear",
        "sleep",
        "power-off",
        "restart",
        "install",
    }
    assert CAPABILITIES.supported_actions({"model": "CHECKERS"}) == {
        "refresh",
        "full-refresh",
        "next",
        "previous",
        "overview",
        "clear",
        "sleep",
        "power-off",
        "restart",
    }
    assert CAPABILITIES.supported_actions({"model": "ESP32-S3-LCD"}) == {
        "refresh"
    }
    assert CAPABILITIES.supported_actions({"model": "mystery"}) == frozenset()


def test_management_control_fallback_matches_each_legacy_family() -> None:
    x4 = {"model": "XTEINK_X4"}
    note4 = {"model": "ZECTRIX_NOTE4"}
    android = {"model": "CHECKERS"}
    generic = {"model": "ESP32-S3-LCD"}
    unknown = {"model": "mystery"}

    assert CAPABILITIES.management_supports(x4, "opendisplay_policy") is True
    assert CAPABILITIES.management_supports(note4, "opendisplay_policy") is False
    assert CAPABILITIES.management_supports(android, "sleep_policy") is False
    assert CAPABILITIES.management_supports(android, "battery_policy") is False
    assert CAPABILITIES.management_supports(generic, "fleet_policy") is True
    assert CAPABILITIES.management_supports(generic, "battery_policy") is False
    assert CAPABILITIES.management_modes(generic) == ("home_assistant",)
    assert CAPABILITIES.management_modes(unknown) == ()
    assert CAPABILITIES.management_supports(unknown, "provisioning") is False
