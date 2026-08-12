"""Identity and locale controls for FlexDisplay devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.text import TextEntity, TextEntityDescription, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .device_capabilities import management_supports
from .entity import FlexDisplayEntity, setup_dynamic_entities


@dataclass(frozen=True, kw_only=True)
class FlexDisplayTextDescription(TextEntityDescription):
    """Describe a text provisioning field."""

    field: str
    record_key: str


DESCRIPTIONS = (
    FlexDisplayTextDescription(
        key="device_name",
        translation_key="device_name",
        field="name",
        record_key="name",
        native_min=1,
        native_max=64,
    ),
    FlexDisplayTextDescription(
        key="area",
        translation_key="area",
        field="area",
        record_key="area",
        native_min=0,
        native_max=64,
    ),
    FlexDisplayTextDescription(
        key="timezone",
        translation_key="timezone",
        field="timezone",
        record_key="assigned_timezone",
        native_min=1,
        native_max=64,
    ),
)


class FlexDisplayText(FlexDisplayEntity, TextEntity):
    """Edit one Bridge-backed text assignment."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    entity_description: FlexDisplayTextDescription

    def __init__(
        self,
        coordinator: FlexDisplayCoordinator,
        device_id: str,
        description: FlexDisplayTextDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> str:
        """Return the assigned value."""
        return str(self.record.get(self.entity_description.record_key) or "")

    async def async_set_value(self, value: str) -> None:
        """Persist the assignment."""
        await self.coordinator.client.provision(
            self.device_id,
            {self.entity_description.field: value.strip()},
        )
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create text controls for registered devices."""
    del hass

    def entities_for_device(
        coordinator: FlexDisplayCoordinator, device_id: str
    ) -> tuple[FlexDisplayText, ...]:
        record = next(
            (item for item in coordinator.data if item.get("device_id") == device_id),
            {},
        )
        descriptions = tuple(
            description
            for description in DESCRIPTIONS
            if (
                description.key in {"device_name", "area"}
                and management_supports(record, "provisioning")
            )
            or (
                description.key == "timezone"
                and management_supports(record, "sleep_policy")
            )
        )
        return tuple(
            FlexDisplayText(coordinator, device_id, description)
            for description in descriptions
        )

    setup_dynamic_entities(
        entry,
        async_add_entities,
        entities_for_device,
    )
