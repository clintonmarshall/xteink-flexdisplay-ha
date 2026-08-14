"""Numeric fleet controls for FlexDisplay devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .device_capabilities import (
    is_android_receiver,
    management_supports,
    supports_audio,
    supports_frontlight,
)
from .entity import FlexDisplayEntity, setup_dynamic_entities


@dataclass(frozen=True, kw_only=True)
class FlexDisplayNumberDescription(NumberEntityDescription):
    """Describe a numeric provisioning field."""

    field: str
    record_key: str


DESCRIPTIONS = (
    FlexDisplayNumberDescription(
        key="refresh_interval",
        translation_key="refresh_interval",
        field="refresh_interval_seconds",
        record_key="assigned_refresh_interval_seconds",
        native_min_value=60,
        native_max_value=86400,
        native_step=60,
        native_unit_of_measurement=UnitOfTime.SECONDS,
    ),
    FlexDisplayNumberDescription(
        key="manual_sleep_duration",
        translation_key="manual_sleep_duration",
        field="manual_sleep_seconds",
        record_key="assigned_manual_sleep_seconds",
        native_min_value=60,
        native_max_value=86400,
        native_step=60,
        native_unit_of_measurement=UnitOfTime.SECONDS,
    ),
    FlexDisplayNumberDescription(
        key="manual_wake_grace",
        translation_key="manual_wake_grace",
        field="manual_wake_grace_seconds",
        record_key="assigned_manual_wake_grace_seconds",
        native_min_value=0,
        native_max_value=600,
        native_step=10,
        native_unit_of_measurement=UnitOfTime.SECONDS,
    ),
    FlexDisplayNumberDescription(
        key="critical_battery",
        translation_key="critical_battery",
        field="critical_battery_percent",
        record_key="assigned_critical_battery_percent",
        native_min_value=5,
        native_max_value=50,
        native_step=1,
        native_unit_of_measurement="%",
    ),
    FlexDisplayNumberDescription(
        key="low_battery",
        translation_key="low_battery",
        field="low_battery_percent",
        record_key="assigned_low_battery_percent",
        native_min_value=10,
        native_max_value=80,
        native_step=1,
        native_unit_of_measurement="%",
    ),
    FlexDisplayNumberDescription(
        key="low_battery_multiplier",
        translation_key="low_battery_multiplier",
        field="low_battery_multiplier",
        record_key="assigned_low_battery_multiplier",
        native_min_value=1,
        native_max_value=12,
        native_step=1,
    ),
    FlexDisplayNumberDescription(
        key="unchanged_image_multiplier",
        translation_key="unchanged_image_multiplier",
        field="unchanged_image_multiplier",
        record_key="assigned_unchanged_image_multiplier",
        native_min_value=1,
        native_max_value=12,
        native_step=1,
    ),
)


class FlexDisplayNumber(FlexDisplayEntity, NumberEntity):
    """Edit one Bridge-backed numeric policy value."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    entity_description: FlexDisplayNumberDescription

    def __init__(
        self,
        coordinator: FlexDisplayCoordinator,
        device_id: str,
        description: FlexDisplayNumberDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    def _record_supported(self, record: dict) -> bool:
        key = self.entity_description.key
        if key == "refresh_interval":
            return management_supports(record, "fleet_policy")
        if key in {"manual_sleep_duration", "manual_wake_grace"}:
            return management_supports(record, "sleep_policy")
        if key in {"critical_battery", "low_battery", "low_battery_multiplier"}:
            return management_supports(record, "battery_policy")
        if key == "unchanged_image_multiplier":
            return management_supports(record, "rendering_profile")
        return False

    @property
    def native_value(self) -> float | None:
        """Return the assigned policy value."""
        value = self.record.get(self.entity_description.record_key)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Persist a bounded integer policy value."""
        await self.coordinator.client.provision(
            self.device_id,
            {self.entity_description.field: round(value)},
        )
        await self.coordinator.async_request_refresh()


class FlexDisplayVoiceVolume(FlexDisplayEntity, NumberEntity):
    """Control receiver speaker volume."""

    _attr_translation_key = "voice_volume"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_voice_volume"

    def _record_supported(self, record: dict) -> bool:
        return supports_audio(record)

    @property
    def native_value(self) -> float | None:
        value = self.record.get("desired_voice_volume", self.record.get("voice_volume"))
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.voice_settings(
            self.device_id, {"volume": round(value)}
        )
        await self.coordinator.async_request_refresh()


class FlexDisplayScreenBrightness(FlexDisplayEntity, NumberEntity):
    """Control Android receiver screen brightness."""

    _attr_translation_key = "screen_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 5
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_screen_brightness"

    @property
    def native_value(self) -> float | None:
        value = self.record.get(
            "desired_screen_brightness", self.record.get("screen_brightness")
        )
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.display_settings(
            self.device_id, {"brightness": round(value)}
        )
        await self.coordinator.async_request_refresh()


class FlexDisplayFrontlightNumber(FlexDisplayEntity, NumberEntity):
    """Control one admitted X4 Pro frontlight channel."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self,
        coordinator: FlexDisplayCoordinator,
        device_id: str,
        control: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.control = control
        self._attr_translation_key = f"frontlight_{control}"
        self._attr_unique_id = f"{device_id}_frontlight_{control}"

    def _record_supported(self, record: dict) -> bool:
        return supports_frontlight(record, self.control)

    @property
    def native_value(self) -> float | None:
        value = self.record.get(
            f"desired_frontlight_{self.control}",
            self.record.get(f"frontlight_{self.control}"),
        )
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.display_settings(
            self.device_id,
            {f"frontlight_{self.control}": round(value)},
        )
        await self.coordinator.async_request_refresh()


def _entities_for_device(
    coordinator: FlexDisplayCoordinator, device_id: str
) -> tuple[NumberEntity, ...]:
    record = next(
        (item for item in coordinator.data if item.get("device_id") == device_id), {}
    )
    entities: list[NumberEntity] = [
        FlexDisplayNumber(coordinator, device_id, description)
        for description in DESCRIPTIONS
        if (
            description.key == "refresh_interval"
            and management_supports(record, "fleet_policy")
        )
        or (
            description.key in {"manual_sleep_duration", "manual_wake_grace"}
            and management_supports(record, "sleep_policy")
        )
        or (
            description.key
            in {"critical_battery", "low_battery", "low_battery_multiplier"}
            and management_supports(record, "battery_policy")
        )
        or (
            description.key == "unchanged_image_multiplier"
            and management_supports(record, "rendering_profile")
        )
    ]
    if supports_audio(record):
        entities.append(FlexDisplayVoiceVolume(coordinator, device_id))
    if is_android_receiver(record):
        entities.append(FlexDisplayScreenBrightness(coordinator, device_id))
    for control in ("brightness", "warmth"):
        if supports_frontlight(record, control):
            entities.append(
                FlexDisplayFrontlightNumber(
                    coordinator,
                    device_id,
                    control,
                )
            )
    return tuple(entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create numeric controls for registered devices."""
    del hass
    setup_dynamic_entities(
        entry,
        async_add_entities,
        _entities_for_device,
    )
