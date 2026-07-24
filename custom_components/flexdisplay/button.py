"""Command buttons for FlexDisplay devices."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .entity import FlexDisplayEntity


class FlexDisplayRefreshButton(FlexDisplayEntity, ButtonEntity):
    """Queue a refresh for the next device check-in."""

    _attr_translation_key = "refresh"

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_refresh"

    async def async_press(self) -> None:
        """Queue a refresh command."""
        await self.coordinator.client.command(self.device_id, "refresh")
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create refresh buttons for registered devices."""
    del hass
    coordinator: FlexDisplayCoordinator = entry.runtime_data
    async_add_entities(
        FlexDisplayRefreshButton(coordinator, record["device_id"])
        for record in coordinator.data
        if record.get("device_id")
    )
