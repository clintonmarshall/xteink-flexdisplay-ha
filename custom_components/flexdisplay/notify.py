"""Native Home Assistant notification entities for active Android receivers."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FlexDisplayCoordinator
from .device_capabilities import is_android_companion, management_supports
from .entity import FlexDisplayEntity, setup_dynamic_entities


class FlexDisplayNotify(FlexDisplayEntity, NotifyEntity):
    """Send a basic alert while retaining the advanced FlexDisplay action."""

    _attr_translation_key = "display_notification"
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_notification"

    def _record_supported(self, record: dict) -> bool:
        return management_supports(record, "notifications")

    @property
    def available(self) -> bool:
        """A foreground-only phone cannot receive an alert while inactive."""
        return (
            super().available
            and self.record.get("online") is True
            and (
                not is_android_companion(self.record)
                or (
                    self.record.get("foreground_active") is True
                    and bool(self.record.get("foreground_session"))
                )
            )
        )

    async def async_send_message(
        self, message: str, title: str | None = None
    ) -> None:
        """Send a bounded basic notification through the existing Bridge API."""
        await self.coordinator.client.notify(
            self.device_id,
            title=title or "FlexDisplay",
            message=message,
        )
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create notify entities only for trusted notification-capable receivers."""
    del hass

    def entities_for_device(
        coordinator: FlexDisplayCoordinator, device_id: str
    ) -> tuple[FlexDisplayNotify, ...]:
        record = next(
            (item for item in coordinator.data if item.get("device_id") == device_id),
            {},
        )
        if not management_supports(record, "notifications"):
            return ()
        return (FlexDisplayNotify(coordinator, device_id),)

    setup_dynamic_entities(entry, async_add_entities, entities_for_device)
