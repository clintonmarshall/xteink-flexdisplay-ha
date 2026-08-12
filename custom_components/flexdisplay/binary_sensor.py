"""Status binary sensors for FlexDisplay devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .device_capabilities import (
    firmware_manageable,
    management_supports,
    reports_battery,
    reports_usb_power,
    supports_xteink_ota,
)
from .entity import FlexDisplayEntity, setup_dynamic_entities


@dataclass(frozen=True, kw_only=True)
class FlexDisplayBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Bridge-backed binary sensor."""

    value_fn: Callable[[dict], bool]


DESCRIPTIONS = (
    FlexDisplayBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda record: bool(record.get("online")),
    ),
    FlexDisplayBinarySensorDescription(
        key="usb_connected",
        translation_key="usb_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda record: bool(record.get("usb_connected")),
    ),
    FlexDisplayBinarySensorDescription(
        key="sd_ready",
        translation_key="sd_ready",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda record: bool(record.get("sd_ready")),
    ),
    FlexDisplayBinarySensorDescription(
        key="sd_writable",
        translation_key="sd_writable",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda record: bool(record.get("sd_writable")),
    ),
    FlexDisplayBinarySensorDescription(
        key="camera_available",
        translation_key="camera_available",
        value_fn=lambda record: bool(record.get("camera_available")),
    ),
    FlexDisplayBinarySensorDescription(
        key="microphone_available",
        translation_key="microphone_available",
        value_fn=lambda record: bool(record.get("microphone_available")),
    ),
    FlexDisplayBinarySensorDescription(
        key="audio_available",
        translation_key="audio_available",
        value_fn=lambda record: bool(record.get("audio_available")),
    ),
    FlexDisplayBinarySensorDescription(
        key="touch_available",
        translation_key="touch_available",
        value_fn=lambda record: bool(record.get("touch_available")),
    ),
    FlexDisplayBinarySensorDescription(
        key="always_on_available",
        translation_key="always_on_available",
        value_fn=lambda record: bool(record.get("always_on_available")),
    ),
    FlexDisplayBinarySensorDescription(
        key="image_unchanged",
        translation_key="image_unchanged",
        value_fn=lambda record: bool(record.get("image_unchanged")),
    ),
    FlexDisplayBinarySensorDescription(
        key="low_battery",
        translation_key="low_battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        value_fn=lambda record: bool(record.get("low_battery")),
    ),
    FlexDisplayBinarySensorDescription(
        key="ha_error",
        translation_key="home_assistant_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda record: bool(record.get("ha_error")),
    ),
    FlexDisplayBinarySensorDescription(
        key="dashboard_fetch_error",
        translation_key="dashboard_fetch_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda record: bool(record.get("dashboard_fetch_error")),
    ),
    FlexDisplayBinarySensorDescription(
        key="firmware_update_problem",
        translation_key="firmware_update_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda record: record.get("firmware_update_status")
        in {"failed", "cancelled"},
    ),
    FlexDisplayBinarySensorDescription(
        key="microphone_available",
        translation_key="microphone_available",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda record: bool(
            record.get("microphone_available")
            and record.get("microphone_permission")
        ),
    ),
)


class FlexDisplayBinarySensor(FlexDisplayEntity, BinarySensorEntity):
    """Report a boolean device state."""

    entity_description: FlexDisplayBinarySensorDescription

    def __init__(
        self,
        coordinator: FlexDisplayCoordinator,
        device_id: str,
        description: FlexDisplayBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    def _record_supported(self, record: dict) -> bool:
        key = self.entity_description.key
        if key == "usb_connected":
            return reports_usb_power(record)
        if key in {"sd_ready", "sd_writable"}:
            return supports_xteink_ota(record)
        if key == "low_battery":
            return reports_battery(record)
        if key == "firmware_update_problem":
            return firmware_manageable(record)
        if key == "microphone_available":
            return management_supports(record, "microphone")
        return True

    @property
    def is_on(self) -> bool:
        """Return the recent-check-in state."""
        return self.entity_description.value_fn(self.record)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create online sensors for registered devices."""
    del hass

    def entities_for_device(
        coordinator: FlexDisplayCoordinator, device_id: str
    ) -> tuple[FlexDisplayBinarySensor, ...]:
        record = next(
            (item for item in coordinator.data if item.get("device_id") == device_id),
            {},
        )
        descriptions = tuple(
            description
            for description in DESCRIPTIONS
            if not (
                (description.key == "usb_connected" and not reports_usb_power(record))
                or (
                    description.key in {"sd_ready", "sd_writable"}
                    and not supports_xteink_ota(record)
                )
                or (description.key == "low_battery" and not reports_battery(record))
                or (
                    description.key == "firmware_update_problem"
                    and not firmware_manageable(record)
                )
                or (
                    description.key == "microphone_available"
                    and not management_supports(record, "microphone")
                )
            )
        )
        return tuple(
            FlexDisplayBinarySensor(coordinator, device_id, description)
            for description in descriptions
        )

    setup_dynamic_entities(
        entry,
        async_add_entities,
        entities_for_device,
    )
