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


def _load_entity_lifecycle() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "custom_components"
        / "flexdisplay"
        / "entity_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location("ha_entity_lifecycle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPABILITIES = _load_capability_helpers()
LIFECYCLE = _load_entity_lifecycle()


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
        "restart-app",
        "test-chime",
        "volume-up",
        "volume-down",
        "mute",
        "unmute",
        "brightness-up",
        "brightness-down",
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


def test_home_assistant_device_identity_uses_normalized_family_metadata() -> None:
    expected = {
        "XTEINK_X4": ("XTEINK / FlexDisplay", "XTEINK_X4"),
        "ZECTRIX-NOTE4": ("Zectrix / FlexDisplay", "ZECTRIX-NOTE4"),
        "CHECKERS": ("Amazon / FlexDisplay", "CHECKERS"),
        "ESP32-S3-LCD": ("ESP / FlexDisplay", "ESP32-S3-LCD"),
        "mystery": ("FlexDisplay", "mystery"),
    }

    for model, identity in expected.items():
        record = {"model": model}
        assert (
            CAPABILITIES.device_manufacturer(record),
            CAPABILITIES.device_model_label(record),
        ) == identity

    described = {
        "model": "CHECKERS",
        "device_capabilities": {
            "family": "android_receiver",
            "label": "Echo Show 5 (2019)",
            "firmware": {"provider": "android_app"},
        },
    }
    assert CAPABILITIES.device_model_label(described) == "Echo Show 5 (2019)"
    assert CAPABILITIES.is_note4({"model": "ZECTRIX-NOTE4"}) is True


def test_dynamic_entity_reconciliation_adds_new_capabilities_once() -> None:
    class FakeEntity:
        def __init__(self, unique_id: str) -> None:
            self.unique_id = unique_id

    enabled: set[str] = {"UNKNOWN01"}

    def factory(coordinator, device_id: str):
        del coordinator
        return (
            (FakeEntity(f"{device_id}_mode"), FakeEntity(f"{device_id}_policy"))
            if device_id in enabled
            else ()
        )

    records = [{"device_id": "UNKNOWN01"}, {"device_id": "LATER-X4"}]
    known: set[str] = set()
    first = LIFECYCLE.collect_new_entities(None, records, factory, known)
    second = LIFECYCLE.collect_new_entities(None, records, factory, known)
    enabled.add("LATER-X4")
    corrected = LIFECYCLE.collect_new_entities(None, records, factory, known)

    assert [entity.unique_id for entity in first] == [
        "UNKNOWN01_mode",
        "UNKNOWN01_policy",
    ]
    assert second == []
    assert [entity.unique_id for entity in corrected] == [
        "LATER-X4_mode",
        "LATER-X4_policy",
    ]
