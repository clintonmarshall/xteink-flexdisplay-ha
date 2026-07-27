"""Data coordinator for FlexDisplay bridge devices."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FlexDisplayApiClient, FlexDisplayApiError
from .const import EVENT_TYPE

LOGGER = logging.getLogger(__name__)


class FlexDisplayCoordinator(DataUpdateCoordinator[list[dict]]):
    """Poll the bridge for its registered devices."""

    def __init__(self, hass: HomeAssistant, client: FlexDisplayApiClient) -> None:
        super().__init__(
            hass,
            LOGGER,
            name="FlexDisplay",
            update_interval=timedelta(seconds=10),
        )
        self.client = client
        self._hass = hass
        self._seen_button_events: dict[str, set[tuple[int, str, int]]] = {}

    def _fire_new_button_events(self, record: dict) -> None:
        device_id = record.get("device_id")
        if not device_id:
            return
        recent = record.get("recent_button_events") or []
        identities = {
            (
                int(event.get("sequence") or 0),
                str(event.get("button") or ""),
                int(event.get("uptime_ms") or 0),
            )
            for event in recent
        }
        previous = self._seen_button_events.get(device_id)
        self._seen_button_events[device_id] = identities
        if previous is None:
            return
        registry = dr.async_get(self._hass)
        device = registry.async_get_device({("flexdisplay", device_id)})
        for event in recent:
            identity = (
                int(event.get("sequence") or 0),
                str(event.get("button") or ""),
                int(event.get("uptime_ms") or 0),
            )
            if identity in previous:
                continue
            self._hass.bus.async_fire(
                EVENT_TYPE,
                {
                    "device_id": device.id if device else None,
                    "flexdisplay_id": device_id,
                    "type": "button_pressed",
                    "button": event.get("button"),
                    "action": event.get("action"),
                    "gesture": event.get("gesture") or "short",
                    "mode": event.get("mode") or "home_assistant",
                    "sequence": event.get("sequence"),
                    "device_uptime_ms": event.get("uptime_ms"),
                    "received_at": event.get("received_at"),
                    "configured_action": event.get("configured_action"),
                },
            )

    async def _async_update_data(self) -> list[dict]:
        try:
            devices = await self.client.devices()
            registry = dr.async_get(self._hass)
            for record in devices:
                device_id = record.get("device_id")
                if not device_id:
                    continue
                device = registry.async_get_device({("flexdisplay", device_id)})
                if device:
                    registry.async_update_device(
                        device.id,
                        model=str(record.get("model") or "XTEINK"),
                        sw_version=str(record.get("firmware") or "unknown"),
                    )
                self._fire_new_button_events(record)
            return devices
        except FlexDisplayApiError as err:
            raise UpdateFailed(f"Unable to update FlexDisplay devices: {err}") from err
