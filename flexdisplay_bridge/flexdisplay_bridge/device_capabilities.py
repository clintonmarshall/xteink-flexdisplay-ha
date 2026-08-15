"""Trusted device capability descriptors for Bridge and Studio management.

Model aliases identify product families.  Device-reported capabilities may
refine presentation and wake delivery for generic embedded displays, but they
must never grant firmware eligibility.  Keeping that distinction here makes
unknown devices fail closed when fleet-management surfaces consume the
descriptor.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from types import MappingProxyType
from typing import Any

XTEINK_ACTIONS = (
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
)
ANDROID_ACTIONS = tuple(action for action in XTEINK_ACTIONS if action != "install") + (
    "restart-app",
    "test-chime",
    "volume-up",
    "volume-down",
    "mute",
    "unmute",
    "brightness-up",
    "brightness-down",
)
ANDROID_PHONE_ACTIONS = ANDROID_ACTIONS + ("camera-snapshot",)
GENERIC_ACTIONS = ("refresh",)
COLOR_RECEIVER_ACTIONS = ("refresh", "next", "previous", "overview")

XTEINK_MODES = (
    "reader",
    "home_assistant",
    "trmnl",
    "opendisplay",
    "photo_frame",
)
RENDERED_MODES = ("home_assistant", "trmnl", "photo_frame")

_X3_ALIASES = frozenset({"X3", "XTEINKX3"})
_X4_ALIASES = frozenset({"X4", "XTEINKX4"})
_NOTE4_ALIASES = frozenset({"N4", "NOTE4", "ZECTRIXNOTE4"})
_ROOK_ALIASES = frozenset(
    {"ROOK", "ECHOSPOT", "ECHOSPOT2017", "AMAZONECHOSPOT"}
)
_CHECKERS_ALIASES = frozenset(
    {"CHECKERS", "ECHOSHOW5", "ECHOSHOW52019", "AMAZONECHOSHOW5"}
)
_JC3636_ALIASES = frozenset(
    {
        "JC3636",
        "JC3636W518",
        "JC3636W518EN",
        "GUITIONJC3636W518",
        "TAICHIPI",
    }
)
_ANDROID_PHONE_ALIASES = frozenset(
    {"ANDROID", "ANDROIDPHONE", "ANDROIDCOMPANION"}
)
_GENERIC_MODEL_MARKERS = ("ESP32", "ESP8266", "LCD", "OLED", "TFT")
_GENERIC_CAPABILITIES = frozenset(
    {
        "lcd",
        "oled",
        "tft",
        "always-on-color",
        "mains-powered",
        "mqtt-screen-refresh",
        "mqtt-refresh",
        "push-refresh-mqtt",
        "lvgl-ui-v1",
        "rgb565",
    }
)
_MQTT_WAKE_CAPABILITIES = frozenset(
    {"mqtt-screen-refresh", "mqtt-refresh", "push-refresh-mqtt"}
)
_ALWAYS_ON_CAPABILITIES = frozenset(
    {"always-on-color", "always-on", "mains-powered"}
)


@dataclass(frozen=True, slots=True)
class DisplayCapabilities:
    """Physical display traits used for preview and render selection."""

    width: int | None
    height: int | None
    technology: str
    color: bool
    shape: str
    image_format: str
    touch: bool = False


@dataclass(frozen=True, slots=True)
class PowerCapabilities:
    """Power telemetry and policy behavior exposed to management clients."""

    power_class: str
    battery_managed: bool
    reports_battery: bool
    reports_usb_power: bool
    supports_sleep: bool


@dataclass(frozen=True, slots=True)
class DeliveryCapabilities:
    """How a persisted command reaches a device after it is queued."""

    refresh_delivery: str
    supports_push_wake: bool
    supports_long_poll: bool
    supports_mqtt_wake: bool
    supports_opendisplay: bool


@dataclass(frozen=True, slots=True)
class FirmwareCapabilities:
    """Firmware ownership; XTEINK OTA must be explicitly trusted here."""

    provider: str
    manageable: bool
    supports_xteink_ota: bool


@dataclass(frozen=True, slots=True)
class ManagementCapabilities:
    """Actions and editor controls that Studio may safely offer."""

    actions: tuple[str, ...]
    modes: tuple[str, ...]
    supports_provisioning: bool
    supports_dashboard_profiles: bool
    supports_fleet_policy: bool
    supports_battery_policy: bool
    supports_sleep_policy: bool
    supports_rendering_profile: bool
    supports_opendisplay_policy: bool
    supports_screen_history: bool
    supports_page_selection: bool
    supports_interactions: bool
    supports_notifications: bool
    supports_audio: bool
    supports_camera: bool
    supports_microphone: bool
    supports_brightness: bool


@dataclass(frozen=True, slots=True)
class DeviceCapabilityDescriptor:
    """Immutable, JSON-serializable capability contract for one device kind."""

    family: str
    model_key: str
    label: str
    known_model: bool
    display: DisplayCapabilities
    power: PowerCapabilities
    delivery: DeliveryCapabilities
    firmware: FirmwareCapabilities
    management: ManagementCapabilities
    reported_capabilities: tuple[str, ...] = ()

    @property
    def firmware_provider(self) -> str:
        """Return the firmware owner without requiring nested-field knowledge."""

        return self.firmware.provider

    @property
    def supports_xteink_ota(self) -> bool:
        """Return whether this device may enter the X3/X4 OTA pipeline."""

        return self.firmware.supports_xteink_ota

    @property
    def actions(self) -> tuple[str, ...]:
        """Return management actions suitable for capability-gated controls."""

        return self.management.actions

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dictionary with arrays for tuple fields."""

        return _json_compatible(asdict(self))

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize this descriptor for API responses or persisted diagnostics."""

        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


def _management(
    *,
    actions: tuple[str, ...],
    modes: tuple[str, ...],
    fleet_policy: bool,
    battery_policy: bool,
    sleep_policy: bool,
    rendering_profile: bool,
    opendisplay_policy: bool,
    page_selection: bool,
    interactions: bool = False,
    notifications: bool = False,
    audio: bool = False,
    camera: bool = False,
    microphone: bool = False,
    brightness: bool = False,
) -> ManagementCapabilities:
    return ManagementCapabilities(
        actions=actions,
        modes=modes,
        supports_provisioning=True,
        supports_dashboard_profiles=True,
        supports_fleet_policy=fleet_policy,
        supports_battery_policy=battery_policy,
        supports_sleep_policy=sleep_policy,
        supports_rendering_profile=rendering_profile,
        supports_opendisplay_policy=opendisplay_policy,
        supports_screen_history=True,
        supports_page_selection=page_selection,
        supports_interactions=interactions,
        supports_notifications=notifications,
        supports_audio=audio,
        supports_camera=camera,
        supports_microphone=microphone,
        supports_brightness=brightness,
    )


_X3 = DeviceCapabilityDescriptor(
    family="xteink_eink",
    model_key="x3",
    label="XTEINK X3",
    known_model=True,
    display=DisplayCapabilities(528, 792, "eink", False, "rectangular", "bmp"),
    power=PowerCapabilities("battery_managed", True, True, True, True),
    delivery=DeliveryCapabilities("poll", False, False, False, True),
    firmware=FirmwareCapabilities("xteink", True, True),
    management=_management(
        actions=XTEINK_ACTIONS,
        modes=XTEINK_MODES,
        fleet_policy=True,
        battery_policy=True,
        sleep_policy=True,
        rendering_profile=True,
        opendisplay_policy=True,
        page_selection=True,
    ),
)

_X4 = replace(
    _X3,
    model_key="x4",
    label="XTEINK X4",
    display=DisplayCapabilities(480, 800, "eink", False, "rectangular", "png"),
)

_NOTE4 = DeviceCapabilityDescriptor(
    family="note4_eink",
    model_key="note4",
    label="Zectrix Note 4",
    known_model=True,
    display=DisplayCapabilities(400, 300, "eink", False, "rectangular", "bmp"),
    power=PowerCapabilities("battery_managed", True, True, True, True),
    delivery=DeliveryCapabilities("poll", False, False, False, False),
    firmware=FirmwareCapabilities("note4", True, False),
    management=_management(
        actions=XTEINK_ACTIONS,
        modes=RENDERED_MODES,
        fleet_policy=True,
        battery_policy=True,
        sleep_policy=True,
        rendering_profile=True,
        opendisplay_policy=False,
        page_selection=True,
        audio=True,
    ),
)

_ANDROID_MANAGEMENT = _management(
    actions=ANDROID_ACTIONS,
    modes=RENDERED_MODES,
    fleet_policy=True,
    battery_policy=False,
    sleep_policy=False,
    rendering_profile=True,
    opendisplay_policy=False,
    page_selection=True,
    interactions=True,
    notifications=True,
    audio=True,
    microphone=True,
    brightness=True,
)

_ROOK = DeviceCapabilityDescriptor(
    family="android_receiver",
    model_key="rook",
    label="Echo Spot (2017)",
    known_model=True,
    display=DisplayCapabilities(480, 480, "lcd", True, "round", "png", True),
    power=PowerCapabilities("always_on_color", False, False, False, False),
    delivery=DeliveryCapabilities("long_poll", True, True, False, False),
    firmware=FirmwareCapabilities("android_app", False, False),
    management=_ANDROID_MANAGEMENT,
)

_CHECKERS = replace(
    _ROOK,
    model_key="checkers",
    label="Echo Show 5 (2019)",
    display=DisplayCapabilities(960, 480, "lcd", True, "rectangular", "png", True),
)

_JC3636 = DeviceCapabilityDescriptor(
    family="esp_color_receiver",
    model_key="jc3636",
    label="JC3636W518EN",
    known_model=True,
    display=DisplayCapabilities(360, 360, "lcd", True, "round", "lvgl-json", True),
    power=PowerCapabilities("always_on_color", False, False, True, False),
    delivery=DeliveryCapabilities("poll", False, False, False, False),
    # Source exists in the external firmware repository, but Bridge OTA remains
    # unassigned until an independently verified release artifact/partition
    # contract is published for this family.
    firmware=FirmwareCapabilities("external", False, False),
    management=_management(
        actions=COLOR_RECEIVER_ACTIONS,
        modes=("home_assistant",),
        fleet_policy=True,
        battery_policy=False,
        sleep_policy=False,
        rendering_profile=True,
        opendisplay_policy=False,
        page_selection=True,
        interactions=True,
    ),
)

_ANDROID_PHONE = DeviceCapabilityDescriptor(
    family="android_receiver",
    model_key="android_phone",
    label="Android companion",
    known_model=True,
    display=DisplayCapabilities(None, None, "lcd", True, "rectangular", "png", True),
    power=PowerCapabilities("on_demand", True, True, True, False),
    delivery=DeliveryCapabilities("long_poll", True, True, False, False),
    firmware=FirmwareCapabilities("android_app", False, False),
    management=_management(
        actions=ANDROID_PHONE_ACTIONS,
        modes=RENDERED_MODES,
        fleet_policy=True,
        battery_policy=False,
        sleep_policy=False,
        rendering_profile=True,
        opendisplay_policy=False,
        page_selection=True,
        interactions=True,
        notifications=True,
        audio=True,
        camera=True,
        microphone=True,
        brightness=True,
    ),
)

_GENERIC = DeviceCapabilityDescriptor(
    family="generic_embedded",
    model_key="generic_esp_lcd",
    label="Generic embedded display",
    known_model=False,
    display=DisplayCapabilities(None, None, "unknown", False, "rectangular", "png"),
    power=PowerCapabilities("battery_managed", True, False, False, True),
    delivery=DeliveryCapabilities("poll", False, False, False, False),
    firmware=FirmwareCapabilities("none", False, False),
    management=_management(
        actions=GENERIC_ACTIONS,
        modes=("home_assistant",),
        fleet_policy=True,
        battery_policy=False,
        sleep_policy=True,
        rendering_profile=True,
        opendisplay_policy=False,
        page_selection=False,
    ),
)

_UNKNOWN = DeviceCapabilityDescriptor(
    family="unknown",
    model_key="unknown",
    label="Unknown display",
    known_model=False,
    display=DisplayCapabilities(None, None, "unknown", False, "unknown", "png"),
    power=PowerCapabilities("unknown", False, False, False, False),
    delivery=DeliveryCapabilities("poll", False, False, False, False),
    firmware=FirmwareCapabilities("none", False, False),
    management=ManagementCapabilities(
        actions=(),
        modes=(),
        supports_provisioning=False,
        supports_dashboard_profiles=False,
        supports_fleet_policy=False,
        supports_battery_policy=False,
        supports_sleep_policy=False,
        supports_rendering_profile=False,
        supports_opendisplay_policy=False,
        supports_screen_history=True,
        supports_page_selection=False,
        supports_interactions=False,
        supports_notifications=False,
        supports_audio=False,
        supports_camera=False,
        supports_microphone=False,
        supports_brightness=False,
    ),
)


DEVICE_CAPABILITY_REGISTRY: Mapping[str, DeviceCapabilityDescriptor] = MappingProxyType(
    {
        descriptor.model_key: descriptor
        for descriptor in (
            _X3,
            _X4,
            _NOTE4,
            _ROOK,
            _CHECKERS,
            _JC3636,
            _ANDROID_PHONE,
            _GENERIC,
            _UNKNOWN,
        )
    }
)

_MODEL_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        **{alias: "x3" for alias in _X3_ALIASES},
        **{alias: "x4" for alias in _X4_ALIASES},
        **{alias: "note4" for alias in _NOTE4_ALIASES},
        **{alias: "rook" for alias in _ROOK_ALIASES},
        **{alias: "checkers" for alias in _CHECKERS_ALIASES},
        **{alias: "jc3636" for alias in _JC3636_ALIASES},
        **{alias: "android_phone" for alias in _ANDROID_PHONE_ALIASES},
    }
)


def normalize_model(model: str) -> str:
    """Normalize a reported model for exact, separator-insensitive matching."""

    return re.sub(r"[^A-Z0-9]", "", str(model or "").upper())


def resolve_device_capabilities(
    model: str,
    *,
    capabilities: Iterable[str] | str = (),
    width: int | None = None,
    height: int | None = None,
) -> DeviceCapabilityDescriptor:
    """Resolve trusted model identity plus safe reported display refinements.

    Only exact known aliases can select a firmware-bearing descriptor.  Generic
    model markers and display capabilities select the firmware-free embedded
    descriptor; everything else remains unknown.
    """

    normalized_model = normalize_model(model)
    reported = _normalize_capabilities(capabilities)
    model_key = _MODEL_ALIASES.get(normalized_model)
    if model_key is not None:
        descriptor = DEVICE_CAPABILITY_REGISTRY[model_key]
    elif _looks_generic(normalized_model, reported):
        descriptor = _generic_descriptor(normalized_model, reported)
    else:
        descriptor = _UNKNOWN

    selected_width = _dimension(width, descriptor.display.width, "width")
    selected_height = _dimension(height, descriptor.display.height, "height")
    display = replace(
        descriptor.display,
        width=selected_width,
        height=selected_height,
    )
    return replace(
        descriptor,
        display=display,
        reported_capabilities=tuple(sorted(reported)),
    )


def _looks_generic(normalized_model: str, capabilities: frozenset[str]) -> bool:
    return any(marker in normalized_model for marker in _GENERIC_MODEL_MARKERS) or bool(
        capabilities.intersection(_GENERIC_CAPABILITIES)
    )


def _generic_descriptor(
    normalized_model: str,
    capabilities: frozenset[str],
) -> DeviceCapabilityDescriptor:
    technology = (
        "oled"
        if "oled" in capabilities or "OLED" in normalized_model
        else "lcd"
        if capabilities.intersection(
            {"lcd", "tft", "always-on-color", "rgb565", "lvgl-ui-v1"}
        )
        or "LCD" in normalized_model
        or "TFT" in normalized_model
        else "eink"
        if capabilities.intersection({"eink", "e-ink", "epaper", "e-paper"})
        else "unknown"
    )
    color = bool(
        technology in {"lcd", "oled"}
        or capabilities.intersection(
            {"color", "colour", "always-on-color", "rgb565", "lvgl-ui-v1"}
        )
    )
    always_on = bool(color and capabilities.intersection(_ALWAYS_ON_CAPABILITIES))
    mqtt_wake = bool(capabilities.intersection(_MQTT_WAKE_CAPABILITIES))
    touch = "touch" in capabilities
    reports_battery = bool(
        capabilities.intersection({"battery", "battery-telemetry"})
    )
    reports_usb = bool(
        capabilities.intersection({"usb-power", "usb-telemetry", "mains-powered"})
    )
    management = replace(
        _GENERIC.management,
        actions=(
            COLOR_RECEIVER_ACTIONS
            if "lvgl-ui-v1" in capabilities
            else _GENERIC.management.actions
        ),
        supports_battery_policy=reports_battery and not always_on,
        supports_sleep_policy=not always_on,
        supports_page_selection=bool(
            capabilities.intersection({"page-navigation", "lvgl-ui-v1"})
        ),
        supports_interactions=touch
        and bool(capabilities.intersection({"interactions", "lvgl-ui-v1"})),
        supports_notifications="notifications" in capabilities,
        supports_audio="audio" in capabilities,
    )
    return replace(
        _GENERIC,
        display=replace(
            _GENERIC.display,
            technology=technology,
            color=color,
            touch=touch,
        ),
        power=PowerCapabilities(
            "always_on_color" if always_on else "battery_managed",
            not always_on,
            reports_battery,
            reports_usb,
            not always_on,
        ),
        delivery=DeliveryCapabilities(
            "mqtt" if mqtt_wake else "poll",
            mqtt_wake,
            False,
            mqtt_wake,
            False,
        ),
        management=management,
    )


def _normalize_capabilities(capabilities: Iterable[str] | str) -> frozenset[str]:
    selected = (capabilities,) if isinstance(capabilities, str) else capabilities
    normalized: set[str] = set()
    for value in selected:
        for item in str(value or "").split(","):
            capability = item.strip().lower().replace("_", "-")
            if capability:
                normalized.add(capability)
    return frozenset(normalized)


def _dimension(value: int | None, fallback: int | None, name: str) -> int | None:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_compatible(item) for item in value]
    return value


__all__ = [
    "DEVICE_CAPABILITY_REGISTRY",
    "DeliveryCapabilities",
    "DeviceCapabilityDescriptor",
    "DisplayCapabilities",
    "FirmwareCapabilities",
    "ManagementCapabilities",
    "PowerCapabilities",
    "normalize_model",
    "resolve_device_capabilities",
]
