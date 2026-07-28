"""Current e-paper screen images for FlexDisplay devices."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.dt import parse_datetime

from .api import FlexDisplayApiError
from .entity import FlexDisplayEntity, setup_dynamic_entities


class FlexDisplayCurrentScreen(FlexDisplayEntity, ImageEntity):
    """Expose the latest image retained by the Bridge."""

    _attr_translation_key = "current_screen"
    _attr_content_type = "image/png"

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_current_screen"

    @property
    def image_last_updated(self) -> datetime | None:
        """Return the Bridge render time so Home Assistant refreshes the image."""
        return parse_datetime(str(self.record.get("last_screen_refresh_at") or ""))

    async def async_image(self) -> bytes | None:
        """Fetch the most recent rendered image from the local Bridge."""
        try:
            return await self.coordinator.client.current_screen(self.device_id)
        except FlexDisplayApiError:
            return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create current-screen images for registered devices."""
    del hass
    setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (
            FlexDisplayCurrentScreen(coordinator, device_id),
        ),
    )
