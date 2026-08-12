"""Read Bridge device-capability records without trusting legacy unknown models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_X_ACTIONS = frozenset(
    {
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
)
_ANDROID_ACTIONS = _X_ACTIONS - {"install"}
_X3_ALIASES = frozenset({"X3", "XTEINKX3"})
_X4_ALIASES = frozenset({"X4", "XTEINKX4"})
_NOTE4_ALIASES = frozenset({"N4", "NOTE4", "ZECTRIXNOTE4"})
_ANDROID_ALIASES = frozenset(
    {
        "ROOK",
        "ECHOSPOT",
        "ECHOSPOT2017",
        "AMAZONECHOSPOT",
        "CHECKERS",
        "ECHOSHOW5",
        "ECHOSHOW52019",
        "AMAZONECHOSHOW5",
    }
)
_GENERIC_MARKERS = ("ESP32", "ESP8266", "LCD", "OLED", "TFT")


def _model_key(record: Mapping[str, Any]) -> str:
    return "".join(
        character
        for character in str(record.get("model") or "").upper()
        if character.isalnum()
    )


def _descriptor(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("device_capabilities")
    return value if isinstance(value, Mapping) else {}


def _section(record: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = _descriptor(record).get(name)
    return value if isinstance(value, Mapping) else {}


def supported_actions(record: Mapping[str, Any]) -> frozenset[str]:
    """Return actions offered by the Bridge, failing closed for unknown models."""
    value = record.get("supported_actions")
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(action) for action in value)
    value = _section(record, "management").get("actions")
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(action) for action in value)

    model = _model_key(record)
    if model in _X3_ALIASES | _X4_ALIASES | _NOTE4_ALIASES:
        return _X_ACTIONS
    if model in _ANDROID_ALIASES:
        return _ANDROID_ACTIONS
    if any(marker in model for marker in _GENERIC_MARKERS):
        return frozenset({"refresh"})
    return frozenset()


def firmware_provider(record: Mapping[str, Any]) -> str:
    """Return the declared firmware owner, with an exact legacy allowlist."""
    value = _section(record, "firmware").get("provider")
    if value is not None:
        return str(value or "none")
    if "firmware_provider" in record:
        return str(record.get("firmware_provider") or "none")

    model = _model_key(record)
    if model in _X3_ALIASES | _X4_ALIASES:
        return "xteink"
    if model in _NOTE4_ALIASES:
        return "note4"
    if model in _ANDROID_ALIASES:
        return "android_app"
    return "none"


def firmware_manageable(record: Mapping[str, Any]) -> bool:
    """Return whether Home Assistant may offer a firmware Update entity."""
    manageable = _section(record, "firmware").get("manageable")
    if isinstance(manageable, bool):
        return manageable
    return firmware_provider(record) in {"xteink", "note4"}


def supports_xteink_ota(record: Mapping[str, Any]) -> bool:
    """Return trusted eligibility for X3/X4 fleet firmware controls."""
    supported = _section(record, "firmware").get("supports_xteink_ota")
    if isinstance(supported, bool):
        return supported
    return firmware_provider(record) == "xteink"


def reports_battery(record: Mapping[str, Any]) -> bool:
    """Return whether battery entities are meaningful for this family."""
    reported = _section(record, "power").get("reports_battery")
    if isinstance(reported, bool):
        return reported
    return firmware_provider(record) in {"xteink", "note4"}


def reports_usb_power(record: Mapping[str, Any]) -> bool:
    """Return whether USB-power telemetry is meaningful for this family."""
    reported = _section(record, "power").get("reports_usb_power")
    if isinstance(reported, bool):
        return reported
    return firmware_provider(record) in {"xteink", "note4"}


def management_supports(record: Mapping[str, Any], capability: str) -> bool:
    """Read one Bridge management flag with a conservative legacy fallback."""
    field = f"supports_{capability}"
    reported = _section(record, "management").get(field)
    if isinstance(reported, bool):
        return reported

    provider = firmware_provider(record)
    model = _model_key(record)
    generic = any(marker in model for marker in _GENERIC_MARKERS)
    if capability in {"provisioning", "dashboard_profiles", "fleet_policy"}:
        return provider in {"xteink", "note4", "android_app"} or generic
    if capability == "battery_policy":
        return provider in {"xteink", "note4"}
    if capability == "sleep_policy":
        return provider in {"xteink", "note4"} or generic
    if capability == "rendering_profile":
        return provider in {"xteink", "note4", "android_app"} or generic
    if capability == "opendisplay_policy":
        return provider == "xteink"
    if capability in {"screen_history", "page_selection"}:
        return provider in {"xteink", "note4", "android_app"}
    return False


def management_modes(record: Mapping[str, Any]) -> tuple[str, ...]:
    """Return family-compatible modes without inventing one for unknown devices."""
    reported = record.get("available_modes")
    if isinstance(reported, (list, tuple)):
        return tuple(str(mode) for mode in reported if mode)
    reported = _section(record, "management").get("modes")
    if isinstance(reported, (list, tuple)):
        return tuple(str(mode) for mode in reported if mode)

    provider = firmware_provider(record)
    if provider == "xteink":
        return ("reader", "home_assistant", "trmnl", "opendisplay", "photo_frame")
    if provider in {"note4", "android_app"}:
        return ("home_assistant", "trmnl", "photo_frame")
    if any(marker in _model_key(record) for marker in _GENERIC_MARKERS):
        return ("home_assistant",)
    return ()
