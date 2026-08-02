from __future__ import annotations

import json
from types import SimpleNamespace

from flexdisplay_bridge.config import MqttConfig
from flexdisplay_bridge.mqtt_service import MqttService


class FakeMqttClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object, bool]] = []

    def publish(self, topic: str, payload: object, retain: bool = False) -> None:
        self.messages.append((topic, payload, retain))


def _service(commands: list[tuple[str, str, str]] | None = None) -> MqttService:
    captured = commands if commands is not None else []
    service = MqttService(
        MqttConfig(enabled=True, entity_source="mqtt"),
        lambda device_id, command, payload: captured.append(
            (device_id, command, payload)
        ),
    )
    service.client = FakeMqttClient()
    service.connected = True
    return service


def test_flexhub_meshtastic_discovery_and_retained_summary() -> None:
    service = _service()
    service.publish_flexhub(
        {
            "connected": True,
            "url": "http://10.200.10.191",
            "status": {
                "platform_version": "0.35.0",
                "meshtastic": {"node_id": "!6db6dc2c"},
            },
            "meshtastic_console": {
                "last_message": "Testing one two",
                "last_sender": "Showroom",
                "last_channel": 0,
                "last_message_at": "2026-08-03T10:00:00+00:00",
                "unread_count": 3,
            },
        }
    )

    topics = {topic: payload for topic, payload, _retain in service.client.messages}
    assert "homeassistant/event/flexhub/meshtastic_message/config" in topics
    assert "homeassistant/text/flexhub/send_meshtastic/config" in topics
    assert "homeassistant/button/flexhub/clear_meshtastic_unread/config" in topics
    assert "homeassistant/button/flexhub/scan/config" in topics
    assert "homeassistant/button/flexhub/deliver/config" in topics
    text_config = json.loads(
        topics["homeassistant/text/flexhub/send_meshtastic/config"]
    )
    assert text_config["pattern"] == r"^[\x20-\x7E]{1,220}$"
    state = json.loads(topics["flexdisplay/flexhub/meshtastic/state"])
    assert state == {
        "last_message": "Testing one two",
        "last_sender": "Showroom",
        "last_channel": 0,
        "last_message_at": "2026-08-03T10:00:00+00:00",
        "unread_count": 3,
        "last_send_status": "idle",
        "last_send_error": "",
    }


def test_flexhub_message_publishes_event_and_updates_unread() -> None:
    service = _service()
    service.publish_flexhub_message(
        {
            "sequence": 42,
            "direction": "inbound",
            "sender": "!12345678",
            "channel": 2,
            "text": "ALERT: Rack temperature high",
        }
    )

    topics = service.client.messages
    event = next(
        json.loads(payload)
        for topic, payload, retain in topics
        if topic == "flexdisplay/flexhub/meshtastic/event" and not retain
    )
    assert event["event_type"] == "message_received"
    state = json.loads(
        next(
            payload
            for topic, payload, retain in reversed(topics)
            if topic == "flexdisplay/flexhub/meshtastic/state" and retain
        )
    )
    assert state["unread_count"] == 1
    assert state["last_message"] == "ALERT: Rack temperature high"


def test_repeated_synthetic_send_failures_are_not_suppressed() -> None:
    service = _service()
    failure = {
        "direction": "outbound",
        "status": "failed",
        "text": "Hello",
        "error": "Hub unavailable",
    }

    service.publish_flexhub_message(failure)
    service.publish_flexhub_message(failure)

    events = [
        json.loads(payload)
        for topic, payload, retain in service.client.messages
        if topic == "flexdisplay/flexhub/meshtastic/event" and not retain
    ]
    assert [event["event_type"] for event in events] == [
        "message_failed",
        "message_failed",
    ]
    state = service._flexhub_console_state
    assert state["last_send_status"] == "failed"
    assert state["last_send_error"] == "Hub unavailable"


def test_mqtt_plain_text_send_and_clear_are_forwarded() -> None:
    commands: list[tuple[str, str, str]] = []
    service = _service(commands)
    service._flexhub_console_state["unread_count"] = 5

    service._on_message(
        None,
        None,
        SimpleNamespace(
            topic="flexdisplay/flexhub/command/send-meshtastic",
            payload=b"Hello fleet",
        ),
    )
    service._on_message(
        None,
        None,
        SimpleNamespace(
            topic="flexdisplay/flexhub/command/clear-meshtastic-unread",
            payload=b"PRESS",
        ),
    )
    service._on_message(
        None,
        None,
        SimpleNamespace(
            topic="flexdisplay/flexhub/command/scan",
            payload=b"PRESS",
        ),
    )

    assert commands == [
        ("flexhub", "send-meshtastic", "Hello fleet"),
        ("flexhub", "clear-meshtastic-unread", "PRESS"),
        ("flexhub", "scan", "PRESS"),
    ]
    assert service._flexhub_console_state["unread_count"] == 0
