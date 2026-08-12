"""Sensors exposed by the FlexDisplay bridge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.dt import parse_datetime

from .coordinator import FlexDisplayCoordinator
from .device_capabilities import (
    firmware_manageable,
    is_note4,
    reports_battery,
    supports_xteink_ota,
)
from .entity import (
    FlexDisplayEntity,
    FlexHubEntity,
    setup_dynamic_entities,
    setup_flexhub_entities,
)


@dataclass(frozen=True, kw_only=True)
class FlexDisplaySensorDescription(SensorEntityDescription):
    """Describe a bridge-backed sensor."""

    value_fn: Callable[[dict], Any]


DESCRIPTIONS = (
    FlexDisplaySensorDescription(
        key="battery_percent",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda record: record.get("battery_percent"),
    ),
    FlexDisplaySensorDescription(
        key="rssi",
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        value_fn=lambda record: record.get("rssi"),
    ),
    FlexDisplaySensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda record: parse_datetime(record.get("last_seen", "")),
    ),
    FlexDisplaySensorDescription(
        key="firmware",
        translation_key="firmware",
        value_fn=lambda record: record.get("firmware"),
    ),
    FlexDisplaySensorDescription(
        key="mode",
        translation_key="mode",
        value_fn=lambda record: record.get("mode"),
    ),
    FlexDisplaySensorDescription(
        key="dashboard_page_title",
        translation_key="dashboard_page",
        value_fn=lambda record: record.get("dashboard_page_title") or "Overview",
    ),
    FlexDisplaySensorDescription(
        key="dashboard_page_number",
        translation_key="dashboard_page_number",
        value_fn=lambda record: (
            f"{record.get('dashboard_page_number', 1)}/{record.get('dashboard_page_count', 1)}"
        ),
    ),
    FlexDisplaySensorDescription(
        key="dashboard_selection",
        translation_key="dashboard_selection",
        value_fn=lambda record: record.get("dashboard_selection") or "default",
    ),
    FlexDisplaySensorDescription(
        key="photo_album",
        translation_key="photo_album",
        value_fn=lambda record: record.get("photo_album") or "none",
    ),
    FlexDisplaySensorDescription(
        key="photo_filename",
        translation_key="photo_filename",
        value_fn=lambda record: record.get("photo_filename") or "none",
    ),
    FlexDisplaySensorDescription(
        key="photo_position",
        translation_key="photo_position",
        value_fn=lambda record: (
            f"{int(record.get('photo_index', 0)) + 1}/{record.get('photo_count', 0)}"
            if record.get("photo_count")
            else "none"
        ),
    ),
    FlexDisplaySensorDescription(
        key="pending_commands",
        translation_key="pending_commands",
        value_fn=lambda record: (
            ", ".join(record.get("pending_commands") or []) or "none"
        ),
    ),
    FlexDisplaySensorDescription(
        key="last_command_result",
        translation_key="last_command_result",
        value_fn=lambda record: record.get("last_command_result") or "none",
    ),
    FlexDisplaySensorDescription(
        key="policy_sync_state",
        translation_key="policy_sync_state",
        value_fn=lambda record: record.get("policy_sync_state") or "not_managed",
    ),
    FlexDisplaySensorDescription(
        key="policy_revision",
        translation_key="policy_revision",
        value_fn=lambda record: record.get("policy_revision", 0),
    ),
    FlexDisplaySensorDescription(
        key="firmware_rollout_status",
        translation_key="firmware_rollout_status",
        value_fn=lambda record: record.get("firmware_rollout_status") or "not_started",
    ),
    FlexDisplaySensorDescription(
        key="firmware_update_status",
        translation_key="firmware_update_status",
        value_fn=lambda record: record.get("firmware_update_status") or "idle",
    ),
    FlexDisplaySensorDescription(
        key="firmware_update_stage",
        translation_key="firmware_update_stage",
        value_fn=lambda record: record.get("firmware_update_stage") or "idle",
    ),
    FlexDisplaySensorDescription(
        key="firmware_update_percent",
        translation_key="firmware_update_progress",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda record: record.get("firmware_update_percent", 0),
    ),
    FlexDisplaySensorDescription(
        key="firmware_update_error",
        translation_key="firmware_update_error",
        value_fn=lambda record: record.get("firmware_update_error") or "none",
    ),
    FlexDisplaySensorDescription(
        key="firmware_update_error_at",
        translation_key="firmware_update_error_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda record: parse_datetime(
            record.get("firmware_update_error_at", "")
        ),
    ),
    FlexDisplaySensorDescription(
        key="power_state",
        translation_key="power_state",
        value_fn=lambda record: record.get("power_state") or "offline",
    ),
    FlexDisplaySensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement="V",
        value_fn=lambda record: record.get("battery_voltage"),
    ),
    FlexDisplaySensorDescription(
        key="uptime_seconds",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="s",
        value_fn=lambda record: record.get("uptime_seconds"),
    ),
    FlexDisplaySensorDescription(
        key="free_heap",
        translation_key="free_heap",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="B",
        value_fn=lambda record: record.get("free_heap"),
    ),
    FlexDisplaySensorDescription(
        key="min_free_heap",
        translation_key="minimum_free_heap",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="B",
        value_fn=lambda record: record.get("min_free_heap"),
    ),
    FlexDisplaySensorDescription(
        key="open_display_last_transport",
        translation_key="open_display_last_transport",
        value_fn=lambda record: record.get("open_display_last_transport") or "none",
    ),
    FlexDisplaySensorDescription(
        key="open_display_fallback",
        translation_key="open_display_fallback",
        value_fn=lambda record: record.get("open_display_fallback") or "none",
    ),
    FlexDisplaySensorDescription(
        key="open_display_min_free_heap",
        translation_key="open_display_min_free_heap",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="B",
        value_fn=lambda record: record.get("open_display_min_free_heap"),
    ),
    FlexDisplaySensorDescription(
        key="open_display_min_largest_block",
        translation_key="open_display_min_largest_block",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="B",
        value_fn=lambda record: record.get("open_display_min_largest_block"),
    ),
    FlexDisplaySensorDescription(
        key="open_display_lan_memory_blocked",
        translation_key="open_display_lan_memory_blocked",
        value_fn=lambda record: str(
            bool(record.get("open_display_lan_memory_blocked"))
        ).lower(),
    ),
    FlexDisplaySensorDescription(
        key="wake_reason",
        translation_key="wake_reason",
        value_fn=lambda record: record.get("wake_reason") or "unknown",
    ),
    FlexDisplaySensorDescription(
        key="last_button",
        translation_key="last_button",
        value_fn=lambda record: record.get("last_button") or "none",
    ),
    FlexDisplaySensorDescription(
        key="last_button_gesture",
        translation_key="last_button_gesture",
        value_fn=lambda record: record.get("last_button_gesture") or "none",
    ),
    FlexDisplaySensorDescription(
        key="last_button_action_result",
        translation_key="last_button_action_result",
        value_fn=lambda record: record.get("last_button_action_result") or "none",
    ),
    FlexDisplaySensorDescription(
        key="last_button_at",
        translation_key="last_button_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda record: parse_datetime(record.get("last_button_at", "")),
    ),
    FlexDisplaySensorDescription(
        key="button_press_count",
        translation_key="button_press_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda record: record.get("button_press_count", 0),
    ),
    FlexDisplaySensorDescription(
        key="sleep_action",
        translation_key="sleep_action",
        value_fn=lambda record: record.get("sleep_action") or "unknown",
    ),
    FlexDisplaySensorDescription(
        key="sleep_reason",
        translation_key="sleep_reason",
        value_fn=lambda record: record.get("sleep_reason") or "unknown",
    ),
    FlexDisplaySensorDescription(
        key="sleep_seconds",
        translation_key="sleep_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="s",
        value_fn=lambda record: record.get("sleep_seconds"),
    ),
    FlexDisplaySensorDescription(
        key="next_wake_at",
        translation_key="next_wake",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda record: parse_datetime(record.get("next_wake_at", "")),
    ),
    FlexDisplaySensorDescription(
        key="health_state",
        translation_key="health",
        value_fn=lambda record: record.get("health_state") or "unknown",
    ),
    FlexDisplaySensorDescription(
        key="health_detail",
        translation_key="health_detail",
        value_fn=lambda record: record.get("health_detail") or "No issues",
    ),
    FlexDisplaySensorDescription(
        key="last_screen_refresh_at",
        translation_key="last_screen_refresh",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda record: parse_datetime(
            record.get("last_screen_refresh_at", "")
        ),
    ),
    FlexDisplaySensorDescription(
        key="screen_history_count",
        translation_key="screen_history_count",
        value_fn=lambda record: record.get("screen_history_count", 0),
    ),
    FlexDisplaySensorDescription(
        key="last_management_action_detail",
        translation_key="last_management_action",
        value_fn=lambda record: record.get("last_management_action_detail") or "none",
    ),
    FlexDisplaySensorDescription(
        key="firmware_install_blockers",
        translation_key="firmware_install_blockers",
        value_fn=lambda record: (
            "; ".join(
                str(blocker)
                for blocker in (record.get("firmware_install_blockers") or [])
            )
            or "none"
        ),
    ),
    FlexDisplaySensorDescription(
        key="screen_brightness",
        translation_key="screen_brightness",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda record: record.get(
            "desired_screen_brightness", record.get("screen_brightness")
        ),
    ),
)

VOICE_DESCRIPTIONS = (
    FlexDisplaySensorDescription(
        key="last_voice_transcript",
        translation_key="last_voice_transcript",
        value_fn=lambda record: record.get("last_voice_transcript") or "none",
    ),
    FlexDisplaySensorDescription(
        key="last_voice_response",
        translation_key="last_voice_response",
        value_fn=lambda record: record.get("last_voice_response") or "none",
    ),
    FlexDisplaySensorDescription(
        key="last_voice_at",
        translation_key="last_voice_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda record: parse_datetime(record.get("last_voice_at", "")),
    ),
)


def _device_sensors(
    coordinator: FlexDisplayCoordinator, device_id: str
) -> tuple[FlexDisplaySensor, ...]:
    descriptions = list(DESCRIPTIONS)
    record = next(
        (item for item in coordinator.data if item.get("device_id") == device_id), {}
    )
    battery_keys = {"battery_percent", "battery_voltage"}
    managed_firmware_keys = {
        "firmware_update_status",
        "firmware_update_stage",
        "firmware_update_percent",
        "firmware_update_error",
        "firmware_update_error_at",
        "firmware_install_blockers",
    }
    descriptions = [
        description
        for description in descriptions
        if not (
            (description.key in battery_keys and not reports_battery(record))
            or (
                description.key in managed_firmware_keys
                and not firmware_manageable(record)
            )
            or (
                description.key == "firmware_rollout_status"
                and not supports_xteink_ota(record)
            )
        )
    ]
    model = str(record.get("model") or "").upper()
    if is_note4(record) or model in {"ROOK", "CHECKERS", "ECHO SPOT", "ECHO SHOW 5"}:
        descriptions.extend(VOICE_DESCRIPTIONS)
    return tuple(
        FlexDisplaySensor(coordinator, device_id, description)
        for description in descriptions
    )


class FlexDisplaySensor(FlexDisplayEntity, SensorEntity):
    """Representation of one FlexDisplay value."""

    entity_description: FlexDisplaySensorDescription

    def __init__(
        self,
        coordinator: FlexDisplayCoordinator,
        device_id: str,
        description: FlexDisplaySensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    def _record_supported(self, record: dict) -> bool:
        key = self.entity_description.key
        if key in {"battery_percent", "battery_voltage"}:
            return reports_battery(record)
        if key in {
            "firmware_update_status",
            "firmware_update_stage",
            "firmware_update_percent",
            "firmware_update_error",
            "firmware_update_error_at",
            "firmware_install_blockers",
        }:
            return firmware_manageable(record)
        if key == "firmware_rollout_status":
            return supports_xteink_ota(record)
        if key.startswith("voice_") or key in {
            "last_voice_request",
            "last_voice_response",
            "last_voice_error",
            "last_voice_at",
        }:
            return is_note4(record)
        return True

    @property
    def native_value(self) -> Any:
        """Return the latest value."""
        return self.entity_description.value_fn(self.record)


@dataclass(frozen=True, kw_only=True)
class FlexHubSensorDescription(SensorEntityDescription):
    """Describe one Meshtastic console sensor."""

    value_fn: Callable[[FlexDisplayCoordinator], Any]


def _meshtastic_message_time(coordinator: FlexDisplayCoordinator) -> datetime | None:
    """Normalize either an ISO timestamp or a device epoch value."""
    message = coordinator.last_meshtastic_message
    raw = message.get("received_at") or message.get("timestamp") or message.get("time")
    if not message.get("received_at") and message.get("timestamp_source") == "uptime":
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return parse_datetime(str(raw or ""))


FLEXHUB_DESCRIPTIONS = (
    FlexHubSensorDescription(
        key="meshtastic_last_message",
        translation_key="meshtastic_last_message",
        value_fn=lambda coordinator: (
            coordinator.last_meshtastic_message.get("text") or "none"
        ),
    ),
    FlexHubSensorDescription(
        key="meshtastic_last_sender",
        translation_key="meshtastic_last_sender",
        value_fn=lambda coordinator: (
            coordinator.last_meshtastic_message.get("sender_name")
            or coordinator.last_meshtastic_message.get("sender")
            or coordinator.last_meshtastic_message.get("from")
            or "none"
        ),
    ),
    FlexHubSensorDescription(
        key="meshtastic_last_channel",
        translation_key="meshtastic_last_channel",
        value_fn=lambda coordinator: (
            coordinator.last_meshtastic_message.get("channel_name")
            or coordinator.last_meshtastic_message.get("channel")
            or 0
        ),
    ),
    FlexHubSensorDescription(
        key="meshtastic_last_message_time",
        translation_key="meshtastic_last_message_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_meshtastic_message_time,
    ),
    FlexHubSensorDescription(
        key="meshtastic_unread_count",
        translation_key="meshtastic_unread_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: coordinator.meshtastic_unread_count,
    ),
)


class FlexHubSensor(FlexHubEntity, SensorEntity):
    """A message or unread state from the SenseCAP Meshtastic gateway."""

    entity_description: FlexHubSensorDescription

    def __init__(
        self,
        coordinator: FlexDisplayCoordinator,
        description: FlexHubSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.flexhub_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the latest console value."""
        return self.entity_description.value_fn(self.coordinator)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensors for devices already registered with the bridge."""
    del hass
    setup_dynamic_entities(
        entry,
        async_add_entities,
        _device_sensors,
    )
    setup_flexhub_entities(
        entry,
        async_add_entities,
        lambda coordinator: (
            FlexHubSensor(coordinator, description)
            for description in FLEXHUB_DESCRIPTIONS
        ),
    )
