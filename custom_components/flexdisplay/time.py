"""Active-hours controls for FlexDisplay devices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .device_capabilities import management_supports
from .entity import FlexDisplayEntity, setup_dynamic_entities


@dataclass(frozen=True, kw_only=True)
class FlexDisplayTimeDescription(TimeEntityDescription):
    """Describe an HH:MM provisioning field."""

    field: str
    record_key: str


DESCRIPTIONS = (
    FlexDisplayTimeDescription(
        key="active_start",
        translation_key="active_start",
        field="active_start",
        record_key="assigned_active_start",
    ),
    FlexDisplayTimeDescription(
        key="active_end",
        translation_key="active_end",
        field="active_end",
        record_key="assigned_active_end",
    ),
)


class FlexDisplayActiveTime(FlexDisplayEntity, TimeEntity):
    """Edit one active-hours boundary."""

    _attr_entity_category = EntityCategory.CONFIG
    entity_description: FlexDisplayTimeDescription

    def __init__(
        self,
        coordinator: FlexDisplayCoordinator,
        device_id: str,
        description: FlexDisplayTimeDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    def _record_supported(self, record: dict) -> bool:
        return management_supports(record, "sleep_policy")

    @property
    def native_value(self) -> time | None:
        """Return the assigned local time."""
        value = str(self.record.get(self.entity_description.record_key) or "")
        try:
            return time.fromisoformat(value)
        except ValueError:
            return None

    async def async_set_value(self, value: time) -> None:
        """Persist the local HH:MM value."""
        await self.coordinator.client.provision(
            self.device_id,
            {self.entity_description.field: value.strftime("%H:%M")},
        )
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create active-hours controls for registered devices."""
    del hass

    def entities_for_device(
        coordinator: FlexDisplayCoordinator, device_id: str
    ) -> tuple[FlexDisplayActiveTime, ...]:
        record = next(
            (item for item in coordinator.data if item.get("device_id") == device_id),
            {},
        )
        if not management_supports(record, "sleep_policy"):
            return ()
        return tuple(
            FlexDisplayActiveTime(coordinator, device_id, description)
            for description in DESCRIPTIONS
        )

    setup_dynamic_entities(
        entry,
        async_add_entities,
        entities_for_device,
    )
