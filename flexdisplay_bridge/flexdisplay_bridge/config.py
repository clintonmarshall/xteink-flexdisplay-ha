from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EntityConfig:
    entity_id: str
    label: str
    unit: str = ""


@dataclass(frozen=True)
class DashboardPageConfig:
    title: str
    entities: tuple[EntityConfig, ...] = ()


@dataclass(frozen=True)
class DashboardProfileConfig:
    name: str
    pages: tuple[DashboardPageConfig, ...] = ()
    auto_rotate_seconds: int = 0


@dataclass(frozen=True)
class DeviceConfig:
    name: str
    model: str = ""
    width: int = 480
    height: int = 800
    refresh_interval_seconds: int = 900
    entities: tuple[EntityConfig, ...] = ()
    profile: str = "default"


@dataclass(frozen=True)
class HomeAssistantConfig:
    base_url: str = "http://homeassistant:8123"
    token: str = ""
    verify_tls: bool = True
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class MqttConfig:
    enabled: bool = False
    host: str = "core-mosquitto"
    port: int = 1883
    username: str = ""
    password: str = ""
    discovery_prefix: str = "homeassistant"
    topic_prefix: str = "flexdisplay"


@dataclass(frozen=True)
class FirmwareConfig:
    version: str = ""
    url: str = ""
    sha256: str = ""
    size: int = 0
    minimum_battery_percent: int = 40


@dataclass(frozen=True)
class BridgeConfig:
    title: str = "HOME ASSISTANT"
    state_path: Path = Path("/data/flexdisplay-state.json")
    api_key: str = ""
    home_assistant: HomeAssistantConfig = HomeAssistantConfig()
    mqtt: MqttConfig = MqttConfig()
    firmware: FirmwareConfig = FirmwareConfig()
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
            model=model,
            width=width,
            height=height,
            entities=_merge_entities(self.default_entities, profile_entities),
            profile=self.default_profile,
        )

    def profile(self, device: DeviceConfig) -> DashboardProfileConfig | None:
        return self.profiles.get(device.profile) or self.profiles.get(self.default_profile)


def _entity(value: dict[str, Any]) -> EntityConfig:
    return EntityConfig(
        entity_id=str(value["entity_id"]),
        label=str(value.get("label") or value["entity_id"]),
        unit=str(value.get("unit") or ""),
    )


def _page_entity(value: Any) -> EntityConfig:
    if isinstance(value, str):
        return EntityConfig(value, value)
    if isinstance(value, dict):
        return _entity(value)
    raise ValueError("Dashboard page entities must be entity IDs or mappings")


def _profile(name: str, value: dict[str, Any]) -> DashboardProfileConfig:
    pages = tuple(
        DashboardPageConfig(
            title=str(page.get("title") or f"PAGE {index + 1}").upper(),
            entities=tuple(_page_entity(item) for item in page.get("entities", [])),
        )
        for index, page in enumerate(value.get("pages", []))
        if isinstance(page, dict)
    )
    return DashboardProfileConfig(
        name=name,
        pages=pages,
        auto_rotate_seconds=max(0, min(86400, int(value.get("auto_rotate_seconds", 0)))),
    )


def _merge_entities(*groups: tuple[EntityConfig, ...]) -> tuple[EntityConfig, ...]:
    merged: dict[str, EntityConfig] = {}
    for group in groups:
        for entity in group:
            merged[entity.entity_id] = entity
    return tuple(merged.values())


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
    default_width, default_height = ((480, 800) if model.upper() == "X4" else (528, 792))
    return DeviceConfig(
        name=str(value.get("name") or device_id),
        model=model,
        width=int(value.get("width", default_width)),
        height=int(value.get("height", default_height)),
        refresh_interval_seconds=max(60, min(86400, int(value.get("refresh_interval_seconds", 900)))),
        entities=entities,
        profile=profile_name,
    )


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
        enabled=_env_bool("FLEXDISPLAY_MQTT_ENABLED", bool(mqtt_raw.get("enabled", False))),
        host=os.getenv("FLEXDISPLAY_MQTT_HOST", str(mqtt_raw.get("host") or "core-mosquitto")),
        port=int(os.getenv("FLEXDISPLAY_MQTT_PORT", mqtt_raw.get("port", 1883))),
        username=os.getenv("FLEXDISPLAY_MQTT_USERNAME", str(mqtt_raw.get("username") or "")),
        password=os.getenv(mqtt_password_env, str(mqtt_raw.get("password") or "")),
        discovery_prefix=str(mqtt_raw.get("discovery_prefix") or "homeassistant").strip("/"),
        topic_prefix=str(mqtt_raw.get("topic_prefix") or "flexdisplay").strip("/"),
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
    return BridgeConfig(
        title=os.getenv("FLEXDISPLAY_DASHBOARD_TITLE", str(raw.get("dashboard", {}).get("title") or "HOME ASSISTANT")),
        state_path=state_path,
        api_key=os.getenv(api_key_env, str(raw.get("server", {}).get("api_key") or "")),
        home_assistant=ha,
        mqtt=mqtt,
        firmware=firmware,
        default_entities=defaults,
        default_profile=default_profile,
        profiles=profiles,
        devices=devices,
    )
