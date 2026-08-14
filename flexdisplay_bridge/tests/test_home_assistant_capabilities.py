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
        "X4_PRO": ("none", False, False),
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
    x4_pro = {"model": "X4_PRO", "firmware_provider": "xteink"}

    assert CAPABILITIES.management_supports(x4, "opendisplay_policy") is True
    assert CAPABILITIES.management_supports(note4, "opendisplay_policy") is False
    assert CAPABILITIES.management_supports(android, "sleep_policy") is False
    assert CAPABILITIES.management_supports(android, "battery_policy") is False
    assert CAPABILITIES.management_supports(generic, "fleet_policy") is True
    assert CAPABILITIES.management_supports(generic, "battery_policy") is False
    assert CAPABILITIES.management_modes(generic) == ("home_assistant",)
    assert CAPABILITIES.management_modes(unknown) == ()
    assert CAPABILITIES.management_supports(unknown, "provisioning") is False
    assert CAPABILITIES.management_supports(x4_pro, "fleet_policy") is False
    assert CAPABILITIES.management_modes(x4_pro) == ()


def test_x4_pro_surfaces_require_explicit_bridge_capabilities() -> None:
    p4 = {
        "model": "X4_PRO",
        "firmware_provider": "xteink",
        "device_capabilities": {
            "firmware": {
                "provider": "xteink",
                "manageable": False,
                "supports_xteink_ota": False,
            },
            "management": {"supports_fleet_policy": False},
            "inputs": {"event_types": []},
            "frontlight": {
                "available": None,
                "supports_on": False,
                "supports_brightness": False,
                "supports_warmth": False,
            },
        },
    }
    s3 = {
        **p4,
        "device_capabilities": {
            **p4["device_capabilities"],
            "inputs": {
                "event_types": ["home", "side_previous", "side_next", "power"]
            },
            "frontlight": {
                "available": True,
                "supports_on": True,
                "supports_brightness": True,
                "supports_warmth": True,
            },
        },
    }

    assert CAPABILITIES.supported_actions(p4) == frozenset()
    assert CAPABILITIES.input_event_types(p4) == ()
    assert CAPABILITIES.supports_frontlight(p4, "brightness") is False
    assert CAPABILITIES.input_event_types(s3) == (
        "home",
        "side_previous",
        "side_next",
        "power",
    )
    assert CAPABILITIES.supports_frontlight(s3, "on") is True
    assert CAPABILITIES.supports_frontlight(s3, "brightness") is True
    assert CAPABILITIES.supports_frontlight(s3, "warmth") is True


def test_button_event_contract_refreshes_types_and_availability_in_place() -> None:
    p4 = {
        "device_capabilities": {"inputs": {"event_types": []}},
    }
    s3 = {
        "device_capabilities": {
            "inputs": {
                "event_types": ["home", "side_previous", "side_next", "power"]
            }
        },
    }
    legacy_x4 = {"model": "XTEINK_X4"}

    class EventProbe(CAPABILITIES.DynamicInputEventContract):
        def __init__(self, record: dict) -> None:
            self.current_record = record

        @property
        def record(self) -> dict:
            return self.current_record

    probe = EventProbe(s3)
    assert probe.event_types == ["home", "side_previous", "side_next", "power"]
    assert probe._record_supported(s3) is True

    probe.current_record = p4
    assert probe.event_types == []
    assert probe._record_supported(p4) is False

    probe.current_record = legacy_x4
    assert probe.event_types == [
        "back",
        "confirm",
        "left",
        "right",
        "up",
        "down",
        "power",
    ]
    assert probe._record_supported(legacy_x4) is True


def test_read_only_device_acquires_event_entity_once_after_s3_admission() -> None:
    class FakeEntity:
        def __init__(self, device_id: str) -> None:
            self.unique_id = f"{device_id}_physical_button"

    def factory(_coordinator, device_id: str):
        record = records[0]
        return (
            (FakeEntity(device_id),)
            if CAPABILITIES.input_event_types(record)
            else ()
        )

    records = [
        {
            "device_id": "PRO-DYNAMIC01",
            "device_capabilities": {"inputs": {"event_types": []}},
        }
    ]
    known: set[str] = set()
    assert LIFECYCLE.collect_new_entities(None, records, factory, known) == []

    records[0]["device_capabilities"]["inputs"]["event_types"] = [
        "home",
        "side_previous",
        "side_next",
        "power",
    ]
    additions = LIFECYCLE.collect_new_entities(None, records, factory, known)
    assert [entity.unique_id for entity in additions] == [
        "PRO-DYNAMIC01_physical_button"
    ]
    assert LIFECYCLE.collect_new_entities(None, records, factory, known) == []


def test_android_audio_controls_are_available_from_family_capabilities() -> None:
    record = {
        "model": "CHECKERS",
        "device_capabilities": {
            "family": "android_receiver",
            "firmware": {"provider": "android_app"},
            "management": {"supports_audio": True},
        },
    }

    assert CAPABILITIES.is_android_receiver(record) is True
    assert CAPABILITIES.supports_audio(record) is True


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
