"""Data coordinator for FlexDisplay bridge devices."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FlexDisplayApiClient, FlexDisplayApiError
from .const import (
    DOMAIN,
    EVENT_TYPE,
    MESHTASTIC_EVENT_TYPE,
    NOTIFICATION_EVENT_TYPE,
)

LOGGER = logging.getLogger(__name__)


class FlexDisplayCoordinator(DataUpdateCoordinator[list[dict]]):
    """Poll the bridge for its registered devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: FlexDisplayApiClient,
        config_entry_id: str,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            name="FlexDisplay",
            update_interval=timedelta(seconds=10),
        )
        self.client = client
        self._hass = hass
        self.config_entry_id = config_entry_id
        self.flexhub_id = f"flexhub_{config_entry_id}"
        self._seen_button_events: dict[str, set[tuple[int, str, int]]] = {}
        self._seen_notification_event_ids: dict[str, set[str]] = {}
        self.flexhub_summary: dict = {}
        self.meshtastic_messages: list[dict] = []
        self.meshtastic_unread_count = 0
        self.last_meshtastic_message: dict = {}
        self._meshtastic_cursor: int | None = None
        self._meshtastic_session_id = ""

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

    def _fire_new_notification_responses(self, record: dict) -> None:
        """Forward only new Bridge-minted public response events after baseline."""
        device_id = str(record.get("device_id") or "")
        if not device_id:
            return
        responses = record.get("notification_response_history") or []
        event_ids = {
            str(item.get("event_id") or "")
            for item in responses
            if isinstance(item, dict)
            and item.get("event_id")
            and not (
                item.get("outcome") == "action"
                and not isinstance(item.get("action_execution_success"), bool)
            )
        }
        previous = self._seen_notification_event_ids.get(device_id)
        self._seen_notification_event_ids[device_id] = event_ids
        if previous is None:
            return
        registry = dr.async_get(self._hass)
        device = registry.async_get_device({(DOMAIN, device_id)})
        allowed = {
            "event_id",
            "notification_id",
            "outcome",
            "action_id",
            "device_reported_at",
            "received_at",
            "trust",
            "action_execution_success",
        }
        for response in responses:
            if not isinstance(response, dict):
                continue
            if response.get("outcome") == "action" and not isinstance(
                response.get("action_execution_success"), bool
            ):
                continue
            event_id = str(response.get("event_id") or "")
            if not event_id or event_id in previous:
                continue
            self._hass.bus.async_fire(
                NOTIFICATION_EVENT_TYPE,
                {
                    key: value for key, value in response.items() if key in allowed
                }
                | {
                    "device_id": device.id if device else None,
                    "flexdisplay_id": device_id,
                },
            )

    @staticmethod
    def _message_sequence(message: dict) -> int:
        """Return a stable sequence from a FlexHub console record."""
        try:
            return max(0, int(message.get("sequence") or 0))
        except (TypeError, ValueError):
            return 0

    def _fire_meshtastic_event(self, message: dict) -> None:
        """Forward one newly observed Meshtastic console record to Home Assistant."""
        registry = dr.async_get(self._hass)
        device = registry.async_get_device({(DOMAIN, self.flexhub_id)})
        direction = str(message.get("direction") or "incoming")
        delivery = str(message.get("delivery_state") or message.get("status") or "")
        event_type = (
            "message_received"
            if direction in {"incoming", "inbound", "received"}
            else "message_failed"
            if delivery in {"failed", "rejected", "timeout"}
            else "message_sent"
        )
        self._hass.bus.async_fire(
            MESHTASTIC_EVENT_TYPE,
            {
                **message,
                "device_id": device.id if device else None,
                "flexdisplay_id": self.flexhub_id,
                "config_entry_id": self.config_entry_id,
                "type": event_type,
            },
        )

    def _accept_meshtastic_messages(self, payload: dict) -> None:
        """Merge a bounded message response and emit only genuinely new records."""
        bridge = (
            payload.get("bridge") if isinstance(payload.get("bridge"), dict) else {}
        )
        console = (
            bridge.get("console") if isinstance(bridge.get("console"), dict) else {}
        )
        raw_unread = console.get("unread_count")
        try:
            authoritative_unread = max(0, int(raw_unread))
        except (TypeError, ValueError):
            authoritative_unread = None
        session_id = str(payload.get("session_id") or payload.get("boot_id") or "")
        session_changed = bool(
            self._meshtastic_session_id
            and session_id
            and session_id != self._meshtastic_session_id
        )
        resetting = session_changed or payload.get("reset") is True
        if resetting:
            self._meshtastic_cursor = 0
            self.meshtastic_messages = []
            self.last_meshtastic_message = {}
        if session_id:
            self._meshtastic_session_id = session_id
        records = payload.get("messages") or []
        if not isinstance(records, list):
            return
        messages = []
        current_session_sequences: set[int] = set()
        for item in records:
            if not isinstance(item, dict):
                continue
            message = dict(item)
            item_session = str(
                message.get("session_id") or message.get("boot_id") or ""
            )
            if resetting and session_id and item_session == session_id:
                current_session_sequences.add(self._message_sequence(message))
            if session_id and not message.get("session_id"):
                message["session_id"] = session_id
            messages.append(message)
        messages.sort(key=self._message_sequence)
        if messages:
            self.last_meshtastic_message = messages[-1]
            self.meshtastic_messages = (self.meshtastic_messages + messages)[-100:]

        newest = max(
            [self._message_sequence(item) for item in messages]
            + [
                self._message_sequence(
                    {
                        "sequence": payload.get("cursor")
                        or payload.get("next_sequence")
                        or payload.get("next_after")
                        or payload.get("latest_sequence")
                        or 0
                    }
                )
            ],
        )
        if authoritative_unread is not None:
            self.meshtastic_unread_count = authoritative_unread
        if self._meshtastic_cursor is None:
            self._meshtastic_cursor = newest
            return

        for message in messages:
            sequence = self._message_sequence(message)
            if sequence <= self._meshtastic_cursor:
                continue
            if resetting and (
                not session_changed or sequence not in current_session_sequences
            ):
                continue
            if authoritative_unread is None and str(
                message.get("direction") or "incoming"
            ) in {
                "incoming",
                "inbound",
                "received",
            }:
                self.meshtastic_unread_count += 1
            self._fire_meshtastic_event(message)
        self._meshtastic_cursor = max(self._meshtastic_cursor, newest)

    def clear_meshtastic_unread(self) -> None:
        """Clear the local unread counter and notify hub entities."""
        self.meshtastic_unread_count = 0
        self.async_update_listeners()

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
                self._fire_new_notification_responses(record)

            try:
                self.flexhub_summary = await self.client.flexhub()
                console = self.flexhub_summary.get("meshtastic_console")
                if isinstance(console, dict):
                    try:
                        self.meshtastic_unread_count = max(
                            0, int(console.get("unread_count") or 0)
                        )
                    except (TypeError, ValueError):
                        pass
                if self.flexhub_summary.get("connected"):
                    messages = await self.client.flexhub_meshtastic_messages(
                        after=self._meshtastic_cursor or 0,
                        limit=32,
                        session_id=self._meshtastic_session_id or None,
                    )
                    self._accept_meshtastic_messages(messages)
            except FlexDisplayApiError as err:
                LOGGER.debug("FlexHub Meshtastic console is unavailable: %s", err)
            return devices
        except FlexDisplayApiError as err:
            raise UpdateFailed(f"Unable to update FlexDisplay devices: {err}") from err
