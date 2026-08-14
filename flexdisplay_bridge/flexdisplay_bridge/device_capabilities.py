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
X4_PRO_ACTIONS = (
    "refresh",
    "full-refresh",
    "next",
    "previous",
    "overview",
    "clear",
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
GENERIC_ACTIONS = ("refresh",)

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
_X4_PRO_ALIASES = frozenset({"X4PRO", "XTEINKX4PRO"})
_NOTE4_ALIASES = frozenset({"N4", "NOTE4", "ZECTRIXNOTE4"})
_ROOK_ALIASES = frozenset(
    {"ROOK", "ECHOSPOT", "ECHOSPOT2017", "AMAZONECHOSPOT"}
)
_CHECKERS_ALIASES = frozenset(
    {"CHECKERS", "ECHOSHOW5", "ECHOSHOW52019", "AMAZONECHOSHOW5"}
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
    touch: bool | None = False


@dataclass(frozen=True, slots=True)
class HardwareCapabilities:
    """Reported hardware identity kept separate from the product model."""

    board_id: str = ""
    hardware_revision: str = ""
    mcu_family: str = ""
    flash_size_bytes: int | None = None
    psram_size_bytes: int | None = None
    reported_identity_complete: bool = False
    management_profile: str = "read_only"


@dataclass(frozen=True, slots=True)
class InputCapabilities:
    """Input topology advertised by an admitted device revision."""

    touch: bool | None = False
    touch_controller: str = ""
    physical_buttons: tuple[str, ...] = ()
    capacitive_buttons: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FrontlightCapabilities:
    """Independent frontlight controls; availability must be explicit."""

    available: bool | None = False
    supports_on: bool = False
    supports_brightness: bool = False
    supports_warmth: bool = False
    minimum: int = 0
    maximum: int = 100


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
    artifact_family: str
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
    supports_button_actions: bool


@dataclass(frozen=True, slots=True)
class DeviceCapabilityDescriptor:
    """Immutable, JSON-serializable capability contract for one device kind."""

    family: str
    model_key: str
    label: str
    known_model: bool
    display: DisplayCapabilities
    hardware: HardwareCapabilities
    inputs: InputCapabilities
    frontlight: FrontlightCapabilities
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
    button_actions: bool = True,
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
        supports_button_actions=button_actions,
    )


_NO_HARDWARE = HardwareCapabilities()
_NO_INPUTS = InputCapabilities()
_NO_FRONTLIGHT = FrontlightCapabilities()
_LEGACY_X_INPUTS = InputCapabilities(
    touch=False,
    physical_buttons=("back", "confirm", "left", "right", "up", "down", "power"),
    event_types=("back", "confirm", "left", "right", "up", "down", "power"),
)


_X3 = DeviceCapabilityDescriptor(
    family="xteink_eink",
    model_key="x3",
    label="XTEINK X3",
    known_model=True,
    display=DisplayCapabilities(528, 792, "eink", False, "rectangular", "bmp"),
    hardware=replace(_NO_HARDWARE, board_id="xteink_x3", management_profile="legacy"),
    inputs=_LEGACY_X_INPUTS,
    frontlight=_NO_FRONTLIGHT,
    power=PowerCapabilities("battery_managed", True, True, True, True),
    delivery=DeliveryCapabilities("poll", False, False, False, True),
    firmware=FirmwareCapabilities("xteink", "x_series", True, True),
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
    hardware=replace(_NO_HARDWARE, board_id="xteink_x4", management_profile="legacy"),
)

_X4_PRO_READ_ONLY_MANAGEMENT = ManagementCapabilities(
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
    supports_button_actions=False,
)

_X4_PRO = DeviceCapabilityDescriptor(
    family="xteink_x4_pro",
    model_key="x4_pro",
    label="XTEINK X4 Pro",
    known_model=True,
    # This is a presentation profile only. Device ingestion and firmware
    # compatibility remain revision-gated below.
    display=DisplayCapabilities(480, 800, "eink", False, "rectangular", "png", None),
    hardware=replace(_NO_HARDWARE, board_id="xteink_x4_pro"),
    inputs=replace(_NO_INPUTS, touch=None),
    frontlight=replace(_NO_FRONTLIGHT, available=None),
    power=PowerCapabilities("unknown", False, False, False, False),
    delivery=DeliveryCapabilities("poll", False, False, False, False),
    firmware=FirmwareCapabilities("xteink", "none", False, False),
    management=_X4_PRO_READ_ONLY_MANAGEMENT,
)

_NOTE4 = DeviceCapabilityDescriptor(
    family="note4_eink",
    model_key="note4",
    label="Zectrix Note 4",
    known_model=True,
    display=DisplayCapabilities(400, 300, "eink", False, "rectangular", "bmp"),
    hardware=replace(_NO_HARDWARE, board_id="zectrix_note4", management_profile="legacy"),
    inputs=_LEGACY_X_INPUTS,
    frontlight=_NO_FRONTLIGHT,
    power=PowerCapabilities("battery_managed", True, True, True, True),
    delivery=DeliveryCapabilities("poll", False, False, False, False),
    firmware=FirmwareCapabilities("note4", "note4", True, False),
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
    button_actions=False,
)

_ROOK = DeviceCapabilityDescriptor(
    family="android_receiver",
    model_key="rook",
    label="Echo Spot (2017)",
    known_model=True,
    display=DisplayCapabilities(480, 480, "lcd", True, "round", "png", True),
    hardware=replace(_NO_HARDWARE, management_profile="android"),
    inputs=replace(_NO_INPUTS, touch=True),
    frontlight=_NO_FRONTLIGHT,
    power=PowerCapabilities("always_on_color", False, False, False, False),
    delivery=DeliveryCapabilities("long_poll", True, True, False, False),
    firmware=FirmwareCapabilities("android_app", "android_app", False, False),
    management=_ANDROID_MANAGEMENT,
)

_CHECKERS = replace(
    _ROOK,
    model_key="checkers",
    label="Echo Show 5 (2019)",
    display=DisplayCapabilities(960, 480, "lcd", True, "rectangular", "png", True),
)

_GENERIC = DeviceCapabilityDescriptor(
    family="generic_embedded",
    model_key="generic_esp_lcd",
    label="Generic embedded display",
    known_model=False,
    display=DisplayCapabilities(None, None, "unknown", False, "rectangular", "png"),
    hardware=_NO_HARDWARE,
    inputs=_NO_INPUTS,
    frontlight=_NO_FRONTLIGHT,
    power=PowerCapabilities("battery_managed", True, False, False, True),
    delivery=DeliveryCapabilities("poll", False, False, False, False),
    firmware=FirmwareCapabilities("none", "none", False, False),
    management=_management(
        actions=GENERIC_ACTIONS,
        modes=("home_assistant",),
        fleet_policy=True,
        battery_policy=False,
        sleep_policy=True,
        rendering_profile=True,
        opendisplay_policy=False,
        page_selection=False,
        button_actions=False,
    ),
)

_UNKNOWN = DeviceCapabilityDescriptor(
    family="unknown",
    model_key="unknown",
    label="Unknown display",
    known_model=False,
    display=DisplayCapabilities(None, None, "unknown", False, "unknown", "png"),
    hardware=_NO_HARDWARE,
    inputs=replace(_NO_INPUTS, touch=None),
    frontlight=replace(_NO_FRONTLIGHT, available=None),
    power=PowerCapabilities("unknown", False, False, False, False),
    delivery=DeliveryCapabilities("poll", False, False, False, False),
    firmware=FirmwareCapabilities("none", "none", False, False),
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
        supports_button_actions=False,
    ),
)


DEVICE_CAPABILITY_REGISTRY: Mapping[str, DeviceCapabilityDescriptor] = MappingProxyType(
    {
        descriptor.model_key: descriptor
        for descriptor in (
            _X3,
            _X4,
            _X4_PRO,
            _NOTE4,
            _ROOK,
            _CHECKERS,
            _GENERIC,
            _UNKNOWN,
        )
    }
)

_MODEL_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        **{alias: "x3" for alias in _X3_ALIASES},
        **{alias: "x4" for alias in _X4_ALIASES},
        **{alias: "x4_pro" for alias in _X4_PRO_ALIASES},
        **{alias: "note4" for alias in _NOTE4_ALIASES},
        **{alias: "rook" for alias in _ROOK_ALIASES},
        **{alias: "checkers" for alias in _CHECKERS_ALIASES},
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
    board_id: str = "",
    hardware_revision: str = "",
    mcu_family: str = "",
    flash_size_bytes: int | None = None,
    psram_size_bytes: int | None = None,
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

    if descriptor.model_key == "x4_pro":
        descriptor = _x4_pro_descriptor(
            descriptor,
            reported,
            board_id=board_id,
            hardware_revision=hardware_revision,
            mcu_family=mcu_family,
            flash_size_bytes=flash_size_bytes,
            psram_size_bytes=psram_size_bytes,
        )

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
        if capabilities.intersection({"lcd", "tft", "always-on-color"})
        or "LCD" in normalized_model
        or "TFT" in normalized_model
        else "eink"
        if capabilities.intersection({"eink", "e-ink", "epaper", "e-paper"})
        else "unknown"
    )
    color = bool(
        technology in {"lcd", "oled"}
        or capabilities.intersection({"color", "colour", "always-on-color"})
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
        supports_battery_policy=reports_battery and not always_on,
        supports_sleep_policy=not always_on,
        supports_page_selection="page-navigation" in capabilities,
        supports_interactions=touch and "interactions" in capabilities,
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
        inputs=replace(_GENERIC.inputs, touch=touch),
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


def _x4_pro_descriptor(
    descriptor: DeviceCapabilityDescriptor,
    capabilities: frozenset[str],
    *,
    board_id: str,
    hardware_revision: str,
    mcu_family: str,
    flash_size_bytes: int | None,
    psram_size_bytes: int | None,
) -> DeviceCapabilityDescriptor:
    """Admit only the exact revision-scoped X4 Pro management profile.

    The confirmed hardware is S3. The upstream management contract is scoped
    to an S3 build that reports all of its identity and capabilities explicitly;
    neither product naming nor panel dimensions can promote another revision
    into that profile.
    """

    selected_board = _identity_value(board_id)
    selected_revision = _identity_value(hardware_revision)
    selected_mcu = _identity_value(mcu_family)
    identity_complete = bool(
        selected_board
        and selected_revision
        and selected_mcu
        and flash_size_bytes is not None
        and psram_size_bytes is not None
    )
    s3_profile = bool(
        selected_board == "xteink-x4-pro"
        and selected_revision == "s3"
        and selected_mcu == "esp32-s3"
        and flash_size_bytes == 16 * 1024 * 1024
        and psram_size_bytes == 8 * 1024 * 1024
    )
    touch = s3_profile and "touch" in capabilities
    capacitive_home = s3_profile and "capacitive-home" in capabilities
    side_buttons = s3_profile and "side-buttons" in capabilities
    frontlight = s3_profile and "frontlight" in capabilities
    physical_buttons = (
        ("side_previous", "side_next", "power") if side_buttons else ()
    )
    capacitive_buttons = ("home",) if capacitive_home else ()
    event_types = (*capacitive_buttons, *physical_buttons)
    management = (
        replace(
            _X4_PRO_READ_ONLY_MANAGEMENT,
            actions=X4_PRO_ACTIONS,
            modes=RENDERED_MODES,
            supports_provisioning=True,
            supports_dashboard_profiles=True,
            supports_fleet_policy=True,
            supports_rendering_profile=True,
            supports_page_selection=True,
            supports_interactions=touch,
        )
        if s3_profile
        else _X4_PRO_READ_ONLY_MANAGEMENT
    )
    return replace(
        descriptor,
        display=replace(descriptor.display, touch=touch if s3_profile else None),
        hardware=HardwareCapabilities(
            board_id=board_id.strip(),
            hardware_revision=hardware_revision.strip(),
            mcu_family=mcu_family.strip(),
            flash_size_bytes=flash_size_bytes,
            psram_size_bytes=psram_size_bytes,
            reported_identity_complete=identity_complete,
            management_profile="s3" if s3_profile else "read_only",
        ),
        inputs=InputCapabilities(
            touch=touch if s3_profile else None,
            touch_controller="gt911" if touch else "",
            physical_buttons=physical_buttons,
            capacitive_buttons=capacitive_buttons,
            event_types=event_types,
        ),
        frontlight=FrontlightCapabilities(
            available=frontlight if s3_profile else None,
            supports_on=frontlight,
            supports_brightness=(
                frontlight and "frontlight-brightness" in capabilities
            ),
            supports_warmth=frontlight and "frontlight-warmth" in capabilities,
        ),
        firmware=(
            replace(descriptor.firmware, artifact_family="x4pro_s3")
            if s3_profile
            else descriptor.firmware
        ),
        management=management,
    )


def _identity_value(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


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
    "FrontlightCapabilities",
    "HardwareCapabilities",
    "InputCapabilities",
    "ManagementCapabilities",
    "PowerCapabilities",
    "normalize_model",
    "resolve_device_capabilities",
]
