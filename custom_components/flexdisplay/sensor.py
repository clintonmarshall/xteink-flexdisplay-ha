"""Sensors exposed by the FlexDisplay bridge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.dt import parse_datetime

from .coordinator import FlexDisplayCoordinator
from .entity import FlexDisplayEntity


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

    @property
    def native_value(self) -> Any:
        """Return the latest value."""
        return self.entity_description.value_fn(self.record)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensors for devices already registered with the bridge."""
    del hass
    coordinator: FlexDisplayCoordinator = entry.runtime_data
    async_add_entities(
        FlexDisplaySensor(coordinator, record["device_id"], description)
        for record in coordinator.data
        if record.get("device_id")
        for description in DESCRIPTIONS
    )
