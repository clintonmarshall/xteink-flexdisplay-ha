"""Command buttons for FlexDisplay devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .entity import FlexDisplayEntity


@dataclass(frozen=True, kw_only=True)
class FlexDisplayButtonDescription(ButtonEntityDescription):
    """Describe a queued FlexDisplay command."""

    command: str


DESCRIPTIONS = (
    FlexDisplayButtonDescription(key="refresh", translation_key="refresh", command="refresh"),
    FlexDisplayButtonDescription(key="next", translation_key="next", command="next"),
    FlexDisplayButtonDescription(key="restart", translation_key="restart", command="restart"),
)


class FlexDisplayCommandButton(FlexDisplayEntity, ButtonEntity):
    """Queue a command for the next device check-in."""

    entity_description: FlexDisplayButtonDescription

    def __init__(
        self,
        coordinator: FlexDisplayCoordinator,
        device_id: str,
        description: FlexDisplayButtonDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    async def async_press(self) -> None:
        """Queue the described command."""
        await self.coordinator.client.command(self.device_id, self.entity_description.command)
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
        FlexDisplayCommandButton(coordinator, record["device_id"], description)
        for record in coordinator.data
        if record.get("device_id")
        for description in DESCRIPTIONS
    )
