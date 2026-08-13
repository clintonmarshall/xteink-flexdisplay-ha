from __future__ import annotations

import os
import math
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .button_actions import ButtonActionValidationError, normalize_action


COLOR_THEMES = {"auto", "midnight", "ocean", "sunrise", "paper"}
COLOR_ROLES = {"auto", "primary", "info", "success", "warning", "danger"}
CONTROL_STYLES = {"auto", "read_only", "button", "toggle"}
TILE_STYLES = {"value", "gauge", "progress", "history", "qr", "image", "name_card"}
TILE_SOURCES = {"home_assistant", "static"}
IMAGE_FITS = {"cover", "contain"}
BADGE_THEMES = {"classic", "bold", "diagonal", "halftone"}
ICONS = {
    "auto", "home", "temperature", "humidity", "battery", "power", "solar",
    "wifi", "storage", "clock", "weather", "rain", "light", "lock", "alert",
}
LAYOUTS = {"auto", "single", "rows", "columns", "grid", "house_pulse"}
BADGE_ASSET_PATTERN = re.compile(r"^[a-f0-9]{24}$")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _choice(value: Any, allowed: set[str], default: str) -> str:
    selected = str(value or default).strip().lower()
    return selected if selected in allowed else default


def _finite_float(value: Any, default: float) -> float:
    try:
        selected = float(value)
    except (TypeError, ValueError):
        return default
    return selected if math.isfinite(selected) else default


def _bounded_text(value: Any, fallback: str, maximum: int) -> str:
    selected = str(value or fallback).replace("\r", " ").replace("\n", " ").strip()
    return selected[:maximum] or fallback


def _bounded_value(value: Any, maximum: int = 1024) -> str:
    selected = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return selected[:maximum]


def _image_url(value: Any) -> str:
    selected = _bounded_text(value, "", 2048)
    if not selected:
        return ""
    parsed = urlparse(selected)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Dashboard image URLs must use http:// or https://")
    if parsed.username or parsed.password:
        raise ValueError("Dashboard image URLs must not contain embedded credentials")
    return selected


@dataclass(frozen=True)
class EntityConfig:
    entity_id: str
    label: str
    unit: str = ""
    icon: str = "auto"
    style: str = "value"
    minimum: float = 0.0
    maximum: float = 100.0
    image_url: str = ""
    image_fit: str = "cover"
    source: str = "home_assistant"
    value: str = ""
    badge_photo_id: str = ""
    badge_photo_filename: str = ""
    badge_theme: str = "classic"
    text_scale: int = 100
    qr_scale: int = 100
    color_role: str = "auto"
    control_style: str = "auto"
    tap_action: dict[str, Any] = field(
        default_factory=lambda: {"type": "none"},
        hash=False,
    )


@dataclass(frozen=True)
class PageActivationConfig:
    type: str = "always"
    entity_id: str = ""
    operator: str = "equals"
    value: str = ""
    priority: int = 0
    expires_after_seconds: int = 0
    start: str = "06:00"
    end: str = "22:00"


@dataclass(frozen=True)
class DashboardPageConfig:
    title: str
    entities: tuple[EntityConfig, ...] = ()
    layout: str = "auto"
    activation: PageActivationConfig = PageActivationConfig()


@dataclass(frozen=True)
class DashboardProfileConfig:
    name: str
    pages: tuple[DashboardPageConfig, ...] = ()
    auto_rotate_seconds: int = 0
    color_theme: str = "auto"


@dataclass(frozen=True)
class DeviceConfig:
    name: str
    area: str = ""
    model: str = ""
    width: int = 480
    height: int = 800
    refresh_interval_seconds: int = 900
    entities: tuple[EntityConfig, ...] = ()
    profile: str = "default"
    mode: str = "home_assistant"
    auto_start: bool = True
    live_mode: bool = False
    manual_sleep_seconds: int = 900
    intelligent_sleep: bool = True
    active_start: str = "06:00"
    active_end: str = "22:00"
    timezone: str = "Australia/Melbourne"
    critical_battery_percent: int = 15
    low_battery_percent: int = 35
    low_battery_multiplier: int = 4
    unchanged_image_multiplier: int = 2
    stay_awake_on_usb: bool = True
    manual_wake_grace_seconds: int = 60
    rendering_profile: str = "standard"
    open_display_transport_policy: str = "auto"


@dataclass(frozen=True)
class ProvisioningConfig:
    enabled: bool = True
    default_area: str = ""
    default_mode: str = "home_assistant"
    auto_start: bool = True
    refresh_interval_seconds: int = 900
    live_mode: bool = False
    manual_sleep_seconds: int = 900
    intelligent_sleep: bool = True
    active_start: str = "06:00"
    active_end: str = "22:00"
    timezone: str = "Australia/Melbourne"
    critical_battery_percent: int = 15
    low_battery_percent: int = 35
    low_battery_multiplier: int = 4
    unchanged_image_multiplier: int = 2
    stay_awake_on_usb: bool = True
    manual_wake_grace_seconds: int = 60
    rendering_profile: str = "standard"
    open_display_transport_policy: str = "auto"


@dataclass(frozen=True)
class HomeAssistantConfig:
    base_url: str = "http://homeassistant:8123"
    token: str = ""
    verify_tls: bool = True
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class MqttConfig:
    enabled: bool = True
    host: str = "core-mosquitto"
    port: int = 1883
    username: str = ""
    password: str = ""
    discovery_prefix: str = "homeassistant"
    topic_prefix: str = "flexdisplay"
    entity_source: str = "mqtt"


@dataclass(frozen=True)
class FlexHubConfig:
    url: str = ""
    access_pin: str = ""
    poll_seconds: int = 15
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class ScreenHistoryConfig:
    enabled: bool = True
    limit: int = 5


@dataclass(frozen=True)
class FirmwareConfig:
    version: str = ""
    url: str = ""
    sha256: str = ""
    size: int = 0
    minimum_battery_percent: int = 40
    canary_required: bool = True
    require_usb_for_canary: bool = True
    max_parallel: int = 1
    retry_limit: int = 3
    retry_backoff_seconds: int = 300
    mirror_enabled: bool = True
    mirror_retry_seconds: int = 300
    stale_install_seconds: int = 1800
    maintenance_window_enabled: bool = False
    maintenance_start: str = "01:00"
    maintenance_end: str = "05:00"
    maintenance_timezone: str = "Australia/Melbourne"
    maintenance_usb_override: bool = True


@dataclass(frozen=True)
class BridgeConfig:
    title: str = "HOME ASSISTANT"
    state_path: Path = Path("/data/flexdisplay-state.json")
    api_key: str = ""
    # Bridge-only HMAC master. Receivers are provisioned with a derived,
    # device-bound 64-character key and never receive this value.
    receiver_key_master: str = ""
    home_assistant: HomeAssistantConfig = HomeAssistantConfig()
    mqtt: MqttConfig = MqttConfig()
    flexhub: FlexHubConfig = FlexHubConfig()
    screen_history: ScreenHistoryConfig = ScreenHistoryConfig()
    firmware: FirmwareConfig = FirmwareConfig()
    note4_firmware: FirmwareConfig = FirmwareConfig(
        minimum_battery_percent=40,
        canary_required=False,
        require_usb_for_canary=False,
        mirror_enabled=False,
    )
    provisioning: ProvisioningConfig = ProvisioningConfig()
    default_entities: tuple[EntityConfig, ...] = ()
    default_profile: str = "default"
    profiles: dict[str, DashboardProfileConfig] = field(default_factory=dict)
    devices: dict[str, DeviceConfig] = field(default_factory=dict)

    def device(self, device_id: str, width: int, height: int, model: str = "") -> DeviceConfig:
        configured = self.devices.get(device_id)
        if configured:
            return configured
        selected_profile = self.profiles.get(self.default_profile)
        profile_entities = tuple(
            entity
            for page in (selected_profile.pages if selected_profile else ())
            for entity in page.entities
        )
        return DeviceConfig(
            name=device_id,
            area=self.provisioning.default_area,
            model=model,
            width=width,
            height=height,
            refresh_interval_seconds=self.provisioning.refresh_interval_seconds,
            entities=_merge_entities(self.default_entities, profile_entities),
            profile=self.default_profile,
            mode=self.provisioning.default_mode,
            auto_start=self.provisioning.auto_start,
            live_mode=self.provisioning.live_mode,
            manual_sleep_seconds=self.provisioning.manual_sleep_seconds,
            intelligent_sleep=self.provisioning.intelligent_sleep,
            active_start=self.provisioning.active_start,
            active_end=self.provisioning.active_end,
            timezone=self.provisioning.timezone,
            critical_battery_percent=self.provisioning.critical_battery_percent,
            low_battery_percent=self.provisioning.low_battery_percent,
            low_battery_multiplier=self.provisioning.low_battery_multiplier,
            unchanged_image_multiplier=self.provisioning.unchanged_image_multiplier,
            stay_awake_on_usb=self.provisioning.stay_awake_on_usb,
            manual_wake_grace_seconds=self.provisioning.manual_wake_grace_seconds,
            rendering_profile=self.provisioning.rendering_profile,
            open_display_transport_policy=self.provisioning.open_display_transport_policy,
        )

    def profile(self, device: DeviceConfig) -> DashboardProfileConfig | None:
        return self.profiles.get(device.profile) or self.profiles.get(self.default_profile)


def _entity(value: dict[str, Any]) -> EntityConfig:
    # Keep hand-authored YAML and Studio/API profiles on one validation path.
    # The local import avoids a module cycle: dashboard_store's model types are
    # defined by this module, while configuration loading happens afterwards.
    from .dashboard_store import DashboardValidationError, parse_profile

    try:
        parsed = parse_profile(
            "config_entity",
            {"pages": [{"title": "CONFIG", "entities": [value]}]},
        )
    except DashboardValidationError as err:
        raise ValueError(f"Invalid dashboard entity: {err}") from err
    return parsed.pages[0].entities[0]


def _page_entity(value: Any) -> EntityConfig:
    if isinstance(value, str):
        return _entity({"entity_id": value, "label": value})
    if isinstance(value, dict):
        return _entity(value)
    raise ValueError("Dashboard page entities must be entity IDs or mappings")


def _page_activation(value: Any) -> PageActivationConfig:
    selected = value if isinstance(value, dict) else {}
    return PageActivationConfig(
        type=str(selected.get("type") or "always"),
        entity_id=str(selected.get("entity_id") or ""),
        operator=str(selected.get("operator") or "equals"),
        value=str(selected.get("value") or ""),
        priority=max(0, min(100, int(selected.get("priority", 0)))),
        expires_after_seconds=max(
            0,
            min(86400, int(selected.get("expires_after_seconds", 0))),
        ),
        start=_clock(selected.get("start"), "06:00"),
        end=_clock(selected.get("end"), "22:00"),
    )


def _profile(name: str, value: dict[str, Any]) -> DashboardProfileConfig:
    from .dashboard_store import DashboardValidationError, parse_profile

    raw_pages = value.get("pages", [])
    if raw_pages == []:
        try:
            rotation = int(value.get("auto_rotate_seconds", 0))
        except (TypeError, ValueError) as err:
            raise ValueError(
                f"Invalid dashboard profile {name!r}: automatic rotation must be seconds"
            ) from err
        return DashboardProfileConfig(
            name=name,
            pages=(),
            auto_rotate_seconds=max(0, min(86400, rotation)),
            color_theme=_choice(value.get("color_theme"), COLOR_THEMES, "auto"),
        )
    if isinstance(raw_pages, list):
        normalized_pages: list[Any] = []
        for page in raw_pages:
            if not isinstance(page, dict):
                normalized_pages.append(page)
                continue
            raw_entities = page.get("entities")
            if isinstance(raw_entities, list):
                normalized_entities = [
                    {"entity_id": item, "label": item}
                    if isinstance(item, str)
                    else item
                    for item in raw_entities
                ]
                normalized_pages.append({**page, "entities": normalized_entities})
            else:
                normalized_pages.append(page)
        value = {**value, "pages": normalized_pages}
    try:
        return parse_profile(name, value)
    except DashboardValidationError as err:
        raise ValueError(f"Invalid dashboard profile {name!r}: {err}") from err


def _merge_entities(*groups: tuple[EntityConfig, ...]) -> tuple[EntityConfig, ...]:
    merged: dict[str, EntityConfig] = {}
    for group in groups:
        for entity in group:
            merged[entity.entity_id] = entity
    return tuple(merged.values())


def _clock(value: Any, default: str) -> str:
    candidate = str(value or default)
    try:
        hour, minute = (int(part) for part in candidate.split(":", 1))
    except (TypeError, ValueError):
        return default
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return default
    return f"{hour:02d}:{minute:02d}"


def _device(
    device_id: str,
    value: dict[str, Any],
    defaults: tuple[EntityConfig, ...],
    profiles: dict[str, DashboardProfileConfig],
    default_profile: str,
) -> DeviceConfig:
    profile_name = str(value.get("profile") or default_profile)
    profile_entities = tuple(
        entity
        for page in (profiles.get(profile_name) or DashboardProfileConfig(profile_name)).pages
        for entity in page.entities
    )
    explicit = tuple(_entity(item) for item in value.get("entities", []))
    entities = _merge_entities(defaults, profile_entities, explicit)
    model = str(value.get("model") or ("X4" if device_id.startswith("X4-") else "X3"))
    default_width, default_height = _default_dimensions(model)
    return DeviceConfig(
        name=str(value.get("name") or device_id),
        area=str(value.get("area") or ""),
        model=model,
        width=int(value.get("width", default_width)),
        height=int(value.get("height", default_height)),
        refresh_interval_seconds=max(60, min(86400, int(value.get("refresh_interval_seconds", 900)))),
        entities=entities,
        profile=profile_name,
        mode=str(value.get("mode") or "home_assistant"),
        auto_start=bool(value.get("auto_start", True)),
        live_mode=bool(value.get("live_mode", False)),
        manual_sleep_seconds=max(60, min(86400, int(value.get("manual_sleep_seconds", 900)))),
        intelligent_sleep=bool(value.get("intelligent_sleep", True)),
        active_start=_clock(value.get("active_start"), "06:00"),
        active_end=_clock(value.get("active_end"), "22:00"),
        timezone=str(value.get("timezone") or "Australia/Melbourne"),
        critical_battery_percent=max(5, min(50, int(value.get("critical_battery_percent", 15)))),
        low_battery_percent=max(10, min(80, int(value.get("low_battery_percent", 35)))),
        low_battery_multiplier=max(1, min(12, int(value.get("low_battery_multiplier", 4)))),
        unchanged_image_multiplier=max(1, min(12, int(value.get("unchanged_image_multiplier", 2)))),
        stay_awake_on_usb=bool(value.get("stay_awake_on_usb", True)),
        manual_wake_grace_seconds=max(0, min(600, int(value.get("manual_wake_grace_seconds", 60)))),
        rendering_profile=_choice(value.get("rendering_profile"), {"standard", "photo"}, "standard"),
        open_display_transport_policy=_choice(
            value.get("open_display_transport_policy"),
            {"auto", "lan_preferred", "ble_only"},
            "auto",
        ),
    )


def _default_dimensions(model: str) -> tuple[int, int]:
    normalized = "".join(character for character in model.upper() if character.isalnum())
    if normalized == "X4":
        return (480, 800)
    if normalized in {"N4", "ZECTRIXNOTE4"}:
        return (400, 300)
    if normalized == "ROOK":
        return (480, 480)
    if normalized in {"CHECKERS", "ECHOSHOW5", "ECHOSHOW52019", "AMAZONECHOSHOW5"}:
        return (960, 480)
    return (528, 792)


def load_config(path: str | Path | None = None) -> BridgeConfig:
    config_path = Path(path or os.getenv("FLEXDISPLAY_CONFIG", "/config/config.yaml"))
    raw: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded

    ha_raw = raw.get("home_assistant") or {}
    token_env = str(ha_raw.get("token_env") or "FLEXDISPLAY_HA_TOKEN")
    ha = HomeAssistantConfig(
        base_url=os.getenv(
            "FLEXDISPLAY_HA_BASE_URL",
            str(ha_raw.get("base_url") or "http://homeassistant:8123"),
        ).rstrip("/"),
        token=os.getenv(token_env, str(ha_raw.get("token") or "")),
        verify_tls=_env_bool("FLEXDISPLAY_HA_VERIFY_TLS", bool(ha_raw.get("verify_tls", True))),
        timeout_seconds=float(os.getenv("FLEXDISPLAY_HA_TIMEOUT_SECONDS", ha_raw.get("timeout_seconds", 10))),
    )

    mqtt_raw = raw.get("mqtt") or {}
    mqtt_password_env = str(mqtt_raw.get("password_env") or "FLEXDISPLAY_MQTT_PASSWORD")
    mqtt = MqttConfig(
        enabled=_env_bool("FLEXDISPLAY_MQTT_ENABLED", bool(mqtt_raw.get("enabled", True))),
        host=os.getenv("FLEXDISPLAY_MQTT_HOST", str(mqtt_raw.get("host") or "core-mosquitto")),
        port=int(os.getenv("FLEXDISPLAY_MQTT_PORT", mqtt_raw.get("port", 1883))),
        username=os.getenv("FLEXDISPLAY_MQTT_USERNAME", str(mqtt_raw.get("username") or "")),
        password=os.getenv(mqtt_password_env, str(mqtt_raw.get("password") or "")),
        discovery_prefix=str(mqtt_raw.get("discovery_prefix") or "homeassistant").strip("/"),
        topic_prefix=str(mqtt_raw.get("topic_prefix") or "flexdisplay").strip("/"),
        entity_source=_choice(
            os.getenv(
                "FLEXDISPLAY_HA_ENTITY_SOURCE",
                str(mqtt_raw.get("entity_source") or "mqtt"),
            ),
            {"hacs", "mqtt", "both"},
            "mqtt",
        ),
    )

    flexhub_raw = raw.get("flexhub") or {}
    flexhub_pin_env = str(flexhub_raw.get("access_pin_env") or "FLEXDISPLAY_FLEXHUB_ACCESS_PIN")
    flexhub = FlexHubConfig(
        url=os.getenv("FLEXDISPLAY_FLEXHUB_URL", str(flexhub_raw.get("url") or "")).rstrip("/"),
        access_pin=os.getenv(flexhub_pin_env, str(flexhub_raw.get("access_pin") or "")),
        poll_seconds=max(
            5,
            min(
                300,
                int(os.getenv("FLEXDISPLAY_FLEXHUB_POLL_SECONDS", flexhub_raw.get("poll_seconds", 15))),
            ),
        ),
        timeout_seconds=max(
            1.0,
            min(
                15.0,
                float(os.getenv("FLEXDISPLAY_FLEXHUB_TIMEOUT_SECONDS", flexhub_raw.get("timeout_seconds", 5))),
            ),
        ),
    )

    screen_history_raw = raw.get("screen_history") or {}
    screen_history = ScreenHistoryConfig(
        enabled=_env_bool(
            "FLEXDISPLAY_SCREEN_HISTORY_ENABLED",
            bool(screen_history_raw.get("enabled", True)),
        ),
        limit=max(
            1,
            min(
                20,
                int(
                    os.getenv(
                        "FLEXDISPLAY_SCREEN_HISTORY_LIMIT",
                        screen_history_raw.get("limit", 5),
                    )
                ),
            ),
        ),
    )

    firmware_raw = raw.get("firmware") or {}
    firmware = FirmwareConfig(
        version=os.getenv("FLEXDISPLAY_FIRMWARE_VERSION", str(firmware_raw.get("version") or "")),
        url=os.getenv("FLEXDISPLAY_FIRMWARE_URL", str(firmware_raw.get("url") or "")),
        sha256=os.getenv("FLEXDISPLAY_FIRMWARE_SHA256", str(firmware_raw.get("sha256") or "")).lower(),
        size=max(0, int(os.getenv("FLEXDISPLAY_FIRMWARE_SIZE", firmware_raw.get("size", 0)))),
        minimum_battery_percent=max(
            20,
            min(
                100,
                int(
                    os.getenv(
                        "FLEXDISPLAY_FIRMWARE_MINIMUM_BATTERY",
                        firmware_raw.get("minimum_battery_percent", 40),
                    )
                ),
            ),
        ),
        canary_required=_env_bool(
            "FLEXDISPLAY_FIRMWARE_CANARY_REQUIRED",
            bool(firmware_raw.get("canary_required", True)),
        ),
        require_usb_for_canary=_env_bool(
            "FLEXDISPLAY_FIRMWARE_REQUIRE_USB_FOR_CANARY",
            bool(firmware_raw.get("require_usb_for_canary", True)),
        ),
        max_parallel=max(
            1,
            min(
                10,
                int(
                    os.getenv(
                        "FLEXDISPLAY_FIRMWARE_MAX_PARALLEL",
                        firmware_raw.get("max_parallel", 1),
                    )
                ),
            ),
        ),
        retry_limit=max(
            0,
            min(
                10,
                int(
                    os.getenv(
                        "FLEXDISPLAY_FIRMWARE_RETRY_LIMIT",
                        firmware_raw.get("retry_limit", 3),
                    )
                ),
            ),
        ),
        retry_backoff_seconds=max(
            0,
            min(
                86400,
                int(
                    os.getenv(
                        "FLEXDISPLAY_FIRMWARE_RETRY_BACKOFF_SECONDS",
                        firmware_raw.get("retry_backoff_seconds", 300),
                    )
                ),
            ),
        ),
        mirror_enabled=_env_bool(
            "FLEXDISPLAY_FIRMWARE_MIRROR_ENABLED",
            bool(firmware_raw.get("mirror_enabled", True)),
        ),
        mirror_retry_seconds=max(
            30,
            min(
                86400,
                int(
                    os.getenv(
                        "FLEXDISPLAY_FIRMWARE_MIRROR_RETRY_SECONDS",
                        firmware_raw.get("mirror_retry_seconds", 300),
                    )
                ),
            ),
        ),
        stale_install_seconds=max(
            300,
            min(
                86400,
                int(
                    os.getenv(
                        "FLEXDISPLAY_FIRMWARE_STALE_INSTALL_SECONDS",
                        firmware_raw.get("stale_install_seconds", 1800),
                    )
                ),
            ),
        ),
        maintenance_window_enabled=_env_bool(
            "FLEXDISPLAY_FIRMWARE_MAINTENANCE_ENABLED",
            bool(firmware_raw.get("maintenance_window_enabled", False)),
        ),
        maintenance_start=str(
            os.getenv(
                "FLEXDISPLAY_FIRMWARE_MAINTENANCE_START",
                firmware_raw.get("maintenance_start", "01:00"),
            )
        ),
        maintenance_end=str(
            os.getenv(
                "FLEXDISPLAY_FIRMWARE_MAINTENANCE_END",
                firmware_raw.get("maintenance_end", "05:00"),
            )
        ),
        maintenance_timezone=str(
            os.getenv(
                "FLEXDISPLAY_FIRMWARE_MAINTENANCE_TIMEZONE",
                firmware_raw.get("maintenance_timezone", "Australia/Melbourne"),
            )
        ),
        maintenance_usb_override=_env_bool(
            "FLEXDISPLAY_FIRMWARE_MAINTENANCE_USB_OVERRIDE",
            bool(firmware_raw.get("maintenance_usb_override", True)),
        ),
    )

    note4_firmware_raw = raw.get("note4_firmware") or {}
    note4_firmware = FirmwareConfig(
        version=os.getenv(
            "FLEXDISPLAY_NOTE4_FIRMWARE_VERSION",
            str(note4_firmware_raw.get("version") or ""),
        ),
        url=os.getenv(
            "FLEXDISPLAY_NOTE4_FIRMWARE_URL",
            str(note4_firmware_raw.get("url") or ""),
        ),
        sha256=os.getenv(
            "FLEXDISPLAY_NOTE4_FIRMWARE_SHA256",
            str(note4_firmware_raw.get("sha256") or ""),
        ).lower(),
        size=max(
            0,
            int(
                os.getenv(
                    "FLEXDISPLAY_NOTE4_FIRMWARE_SIZE",
                    note4_firmware_raw.get("size", 0),
                )
            ),
        ),
        minimum_battery_percent=max(
            20,
            min(
                100,
                int(
                    os.getenv(
                        "FLEXDISPLAY_NOTE4_FIRMWARE_MINIMUM_BATTERY",
                        note4_firmware_raw.get("minimum_battery_percent", 40),
                    )
                ),
            ),
        ),
        canary_required=False,
        require_usb_for_canary=False,
        max_parallel=1,
        retry_limit=3,
        retry_backoff_seconds=300,
        mirror_enabled=False,
        maintenance_window_enabled=False,
    )

    provisioning_raw = raw.get("provisioning") or {}
    provisioning = ProvisioningConfig(
        enabled=bool(provisioning_raw.get("enabled", True)),
        default_area=str(provisioning_raw.get("default_area") or ""),
        default_mode=str(provisioning_raw.get("default_mode") or "home_assistant"),
        auto_start=bool(provisioning_raw.get("auto_start", True)),
        refresh_interval_seconds=max(
            60,
            min(86400, int(provisioning_raw.get("refresh_interval_seconds", 900))),
        ),
        live_mode=bool(provisioning_raw.get("live_mode", False)),
        manual_sleep_seconds=max(
            60,
            min(86400, int(provisioning_raw.get("manual_sleep_seconds", 900))),
        ),
        intelligent_sleep=bool(provisioning_raw.get("intelligent_sleep", True)),
        active_start=_clock(provisioning_raw.get("active_start"), "06:00"),
        active_end=_clock(provisioning_raw.get("active_end"), "22:00"),
        timezone=str(provisioning_raw.get("timezone") or "Australia/Melbourne"),
        critical_battery_percent=max(
            5, min(50, int(provisioning_raw.get("critical_battery_percent", 15)))
        ),
        low_battery_percent=max(
            10, min(80, int(provisioning_raw.get("low_battery_percent", 35)))
        ),
        low_battery_multiplier=max(
            1, min(12, int(provisioning_raw.get("low_battery_multiplier", 4)))
        ),
        unchanged_image_multiplier=max(
            1, min(12, int(provisioning_raw.get("unchanged_image_multiplier", 2)))
        ),
        stay_awake_on_usb=bool(provisioning_raw.get("stay_awake_on_usb", True)),
        manual_wake_grace_seconds=max(
            0, min(600, int(provisioning_raw.get("manual_wake_grace_seconds", 60)))
        ),
        rendering_profile=_choice(
            provisioning_raw.get("rendering_profile"),
            {"standard", "photo"},
            "standard",
        ),
        open_display_transport_policy=_choice(
            provisioning_raw.get("open_display_transport_policy"),
            {"auto", "lan_preferred", "ble_only"},
            "auto",
        ),
    )

    dashboard_raw = raw.get("dashboard") or {}
    defaults = tuple(_entity(item) for item in dashboard_raw.get("entities", []))
    default_profile = str(dashboard_raw.get("default_profile") or "default")
    profiles = {
        str(name): _profile(str(name), value or {})
        for name, value in (dashboard_raw.get("profiles") or {}).items()
    }
    devices = {
        str(device_id): _device(
            str(device_id),
            value or {},
            defaults,
            profiles,
            default_profile,
        )
        for device_id, value in (raw.get("devices") or {}).items()
    }
    state_path = Path(
        os.getenv(
            "FLEXDISPLAY_STATE_PATH",
            str(raw.get("storage", {}).get("state_path") or "/data/flexdisplay-state.json"),
        )
    )
    api_key_env = str(raw.get("server", {}).get("api_key_env") or "FLEXDISPLAY_BRIDGE_API_KEY")
    receiver_master_path = Path(
        str(
            raw.get("server", {}).get("lvgl_receiver_key_master_file")
            or "/data/flexdisplay-lvgl-receiver-master"
        )
    )
    receiver_key_master = ""
    if receiver_master_path.is_symlink():
        raise ValueError("Colour receiver master file must not be a symlink")
    if receiver_master_path.exists():
        metadata = receiver_master_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ValueError(
                "Colour receiver master must be an owner-only regular file owned by this process user"
            )
        if not 16 <= metadata.st_size <= 256:
            raise ValueError("Colour receiver master file must contain 16-256 bytes")
        encoded_master = receiver_master_path.read_bytes()
        try:
            receiver_key_master = encoded_master.decode("utf-8", errors="strict")
        except UnicodeError as err:
            raise ValueError("Colour receiver master file is not valid UTF-8") from err
        if any(
            character < " " or character == "\x7f"
            for character in receiver_key_master
        ):
            raise ValueError("Colour receiver master must not contain control characters")
    return BridgeConfig(
        title=os.getenv("FLEXDISPLAY_DASHBOARD_TITLE", str(raw.get("dashboard", {}).get("title") or "HOME ASSISTANT")),
        state_path=state_path,
        api_key=os.getenv(api_key_env, str(raw.get("server", {}).get("api_key") or "")),
        receiver_key_master=receiver_key_master,
        home_assistant=ha,
        mqtt=mqtt,
        flexhub=flexhub,
        screen_history=screen_history,
        firmware=firmware,
        note4_firmware=note4_firmware,
        provisioning=provisioning,
        default_entities=defaults,
        default_profile=default_profile,
        profiles=profiles,
        devices=devices,
    )
