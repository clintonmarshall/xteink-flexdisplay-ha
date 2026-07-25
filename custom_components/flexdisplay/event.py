"""Physical-button events from FlexDisplay devices."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import BUTTON_EVENT_TYPES, EVENT_TYPE
from .coordinator import FlexDisplayCoordinator
from .entity import FlexDisplayEntity, setup_dynamic_entities


class FlexDisplayButtonEvent(FlexDisplayEntity, EventEntity):
    """Expose physical presses as a native Home Assistant event entity."""

    _attr_translation_key = "physical_button"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types: ClassVar[list[str]] = list(BUTTON_EVENT_TYPES)

    def __init__(self, coordinator: FlexDisplayCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_physical_button"

    async def async_added_to_hass(self) -> None:
        """Listen for button events attributed to this device."""
        await super().async_added_to_hass()

        @callback
        def handle_event(event: Event) -> None:
            if event.data.get("flexdisplay_id") != self.device_id:
                return
            button = event.data.get("button")
            if button not in BUTTON_EVENT_TYPES:
                return
            self._trigger_event(
                button,
                {
                    "action": event.data.get("action"),
                    "sequence": event.data.get("sequence"),
                    "device_uptime_ms": event.data.get("device_uptime_ms"),
                    "received_at": event.data.get("received_at"),
                },
            )
            self.async_write_ha_state()

        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, handle_event))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create physical-button event entities."""
    del hass
    setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (FlexDisplayButtonEvent(coordinator, device_id),),
    )
