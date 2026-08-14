"""Physical-button events from FlexDisplay devices."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import EVENT_TYPE, MESHTASTIC_EVENT_TYPE, MESHTASTIC_EVENT_TYPES
from .coordinator import FlexDisplayCoordinator
from .device_capabilities import DynamicInputEventContract, input_event_types
from .entity import (
    FlexDisplayEntity,
    FlexHubEntity,
    setup_dynamic_entities,
    setup_flexhub_entities,
)


class FlexDisplayButtonEvent(
    DynamicInputEventContract,
    FlexDisplayEntity,
    EventEntity,
):
    """Expose physical presses as a native Home Assistant event entity."""

    _attr_translation_key = "physical_button"
    _attr_device_class = EventDeviceClass.BUTTON

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
            if button not in self.event_types:
                return
            self._trigger_event(
                button,
                {
                    "action": event.data.get("action"),
                    "gesture": event.data.get("gesture"),
                    "mode": event.data.get("mode"),
                    "sequence": event.data.get("sequence"),
                    "device_uptime_ms": event.data.get("device_uptime_ms"),
                    "received_at": event.data.get("received_at"),
                    "configured_action": event.data.get("configured_action"),
                },
            )
            self.async_write_ha_state()

        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, handle_event))


class FlexHubMeshtasticEvent(FlexHubEntity, EventEntity):
    """Expose Meshtastic traffic as a native Home Assistant event entity."""

    _attr_translation_key = "meshtastic_message"
    _attr_event_types: ClassVar[list[str]] = list(MESHTASTIC_EVENT_TYPES)

    def __init__(self, coordinator: FlexDisplayCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.flexhub_id}_meshtastic_message"

    async def async_added_to_hass(self) -> None:
        """Listen for new console messages attributed to the FlexHub."""
        await super().async_added_to_hass()

        @callback
        def handle_event(event: Event) -> None:
            if event.data.get("flexdisplay_id") != self.coordinator.flexhub_id:
                return
            event_type = str(event.data.get("type") or "message_received")
            if event_type not in MESHTASTIC_EVENT_TYPES:
                return
            attributes = {
                key: value
                for key, value in event.data.items()
                if key not in {"device_id", "flexdisplay_id", "type"}
            }
            self._trigger_event(event_type, attributes)
            self.async_write_ha_state()

        self.async_on_remove(
            self.hass.bus.async_listen(MESHTASTIC_EVENT_TYPE, handle_event)
        )


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
        lambda coordinator, device_id: (
            (FlexDisplayButtonEvent(coordinator, device_id),)
            if input_event_types(
                next(
                    (
                        item
                        for item in coordinator.data
                        if item.get("device_id") == device_id
                    ),
                    {},
                )
            )
            else ()
        ),
    )
    setup_flexhub_entities(
        entry,
        async_add_entities,
        lambda coordinator: (FlexHubMeshtasticEvent(coordinator),),
    )
