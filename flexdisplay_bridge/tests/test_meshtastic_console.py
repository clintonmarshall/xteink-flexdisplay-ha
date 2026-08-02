from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient
from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import BridgeConfig
from flexdisplay_bridge.flexhub_client import FlexHubClient, FlexHubClientError
from flexdisplay_bridge.home_assistant import HomeAssistantClient
from flexdisplay_bridge.meshtastic_console import (
    MeshtasticConsoleStore,
    MeshtasticConsoleValidationError,
)
from flexdisplay_bridge.mqtt_service import MqttService


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


def test_client_filters_messages_and_tracks_compact_console_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        calls.append((url, kwargs))
        return FakeResponse(
            {
                "messages": [
                    {
                        "sequence": 17,
                        "direction": "inbound",
                        "sender": "Showroom",
                        "channel": 2,
                        "text": "ALERT: Door opened",
                    }
                ],
                "next_after": 17,
            }
        )

    monkeypatch.setattr(requests, "get", fake_get)
    client = FlexHubClient(tmp_path / "flexhub.json")
    client.configure("http://10.200.40.55", "2468")

    payload, observed = client.fetch_messages(
        after=10,
        limit=15,
        query="door",
        direction="inbound",
        channel=2,
        node="12345678",
    )

    assert payload["next_after"] == 17
    assert observed == []
    assert calls[0][0].endswith("/api/flexhub/meshtastic/messages")
    assert calls[0][1]["params"] == {
        "after": 10,
        "limit": 15,
        "query": "door",
        "direction": "inbound",
        "channel": 2,
        "node": "!12345678",
    }
    assert calls[0][1]["headers"]["X-FlexHub-Token"] == "2468"
    console = client.summary()["meshtastic_console"]
    assert console["last_sender"] == "Showroom"
    assert console["last_channel"] == 2
    assert console["unread_count"] == 0
    assert console["cursor"] == 17
    assert client.fetch_messages(after=10)[1] == []
    client.fetch_messages(limit=500, session_id=42)
    assert calls[-1][1]["params"]["limit"] == 32
    assert calls[-1][1]["params"]["session_id"] == 42
    client.observe_meshtastic_messages(
        {
            "session_id": 42,
            "messages": [
                {
                    "sequence": 18,
                    "direction": "inbound",
                    "sender": "Showroom",
                    "text": "New after baseline",
                }
            ],
        }
    )
    assert client.summary()["meshtastic_console"]["unread_count"] == 1
    assert client.mark_meshtastic_read()["unread_count"] == 0
    with pytest.raises(FlexHubClientError, match="80 UTF-8 bytes"):
        client.fetch_messages(query="🙂" * 21)


def test_client_validates_and_sends_meshtastic_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        calls.append((url, kwargs))
        return FakeResponse({"accepted": True, "packet_id": 123})

    monkeypatch.setattr(requests, "post", fake_post)
    client = FlexHubClient(tmp_path / "flexhub.json")
    client.configure("http://10.200.40.55")

    result = client.send_meshtastic_message(
        {
            "text": "Meet at rack 4",
            "destination": "89abcdef",
            "channel": 1,
            "request_ack": True,
        }
    )

    assert result["packet_id"] == 123
    assert calls[0][0].endswith("/api/flexhub/meshtastic/messages")
    assert calls[0][1]["json"] == {
        "text": "Meet at rack 4",
        "destination": "!89abcdef",
        "channel": 1,
        "request_ack": True,
    }
    with pytest.raises(FlexHubClientError, match="220 UTF-8 bytes"):
        client.send_meshtastic_message({"text": "🙂" * 56})
    with pytest.raises(FlexHubClientError, match="Wait one second") as throttled:
        client.send_meshtastic_message({"text": "Too soon"})
    assert throttled.value.status_code == 429
    with pytest.raises(FlexHubClientError, match="control character"):
        FlexHubClient.normalize_meshtastic_message({"text": "bad\x01message"})
    with pytest.raises(FlexHubClientError, match="valid UTF-8"):
        FlexHubClient.normalize_meshtastic_message({"text": "bad\ud800message"})
    for destination in ("!00000000", "!ffffffff"):
        with pytest.raises(FlexHubClientError, match="reserved node ID"):
            FlexHubClient.normalize_meshtastic_message(
                {"text": "reserved", "destination": destination}
            )


def test_client_observes_outbound_delivery_state_transitions(tmp_path: Path) -> None:
    client = FlexHubClient(tmp_path / "flexhub.json")
    client.observe_meshtastic_messages({"messages": []})
    queued = {
        "messages": [
            {
                "sequence": 2,
                "packet_id": 99,
                "direction": "outbound",
                "text": "Test",
                "status": "queued",
            }
        ]
    }
    acknowledged = {
        "messages": [
            {
                "sequence": 2,
                "packet_id": 99,
                "direction": "outbound",
                "text": "Test",
                "status": "acknowledged",
            }
        ]
    }

    assert client.observe_meshtastic_messages(queued)[0]["status"] == "queued"
    assert (
        client.observe_meshtastic_messages(acknowledged)[0]["status"] == "acknowledged"
    )
    assert client.observe_meshtastic_messages(acknowledged) == []
    assert client.summary()["meshtastic_console"]["unread_count"] == 0


def test_client_preserves_action_conflict_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: FakeResponse({"error": "Hub is busy"}, status_code=409),
    )
    client = FlexHubClient(tmp_path / "flexhub.json")
    client.configure("http://10.200.40.55")

    with pytest.raises(FlexHubClientError, match="Hub is busy") as raised:
        client.action("scan")

    assert raised.value.status_code == 409


def test_console_settings_are_validated_persisted_and_deduplicated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "console.json"
    store = MeshtasticConsoleStore(path)
    payload = store.replace(
        {
            "templates": [
                {
                    "id": "ack",
                    "label": "Acknowledge",
                    "text": "Acknowledged",
                    "destination": "broadcast",
                }
            ],
            "rules": [
                {
                    "id": "alerts",
                    "label": "Fleet alerts",
                    "enabled": True,
                    "match_prefix": "ALERT:",
                    "device_ids": ["X3-ONE", "X4-TWO"],
                    "priority": "critical",
                    "strip_prefix": True,
                }
            ],
        }
    )

    assert payload["templates"][0]["text"] == "Acknowledged"
    message = {
        "sequence": 8,
        "direction": "inbound",
        "channel": 0,
        "text": "alert: Test alarm",
    }
    assert MeshtasticConsoleStore(path).matching_rules(message)[0]["id"] == "alerts"
    assert [item["sequence"] for item in store.claim_messages([message])] == [8]
    assert store.claim_messages([message]) == []
    assert "processed_keys" not in store.payload()

    with pytest.raises(MeshtasticConsoleValidationError, match="target a display"):
        store.replace(
            {
                "templates": [],
                "rules": [{"id": "broken", "match_prefix": "ALERT:", "device_ids": []}],
            }
        )
    notification_only = store.replace(
        {
            "templates": [],
            "rules": [
                {
                    "id": "node-notify",
                    "match_prefix": "ALERT:",
                    "device_ids": [],
                    "node": "12345678",
                    "notify": True,
                    "notify_service": "notify.mobile_app_test",
                }
            ],
        }
    )
    assert notification_only["rules"][0]["node"] == "!12345678"
    assert store.matching_rules({**message, "sender_id": "!12345678"})
    assert store.matching_rules({**message, "sender": "!12345678"})
    assert not store.matching_rules({**message, "sender_id": "!87654321"})


def test_console_store_discards_corrupt_persisted_items(tmp_path: Path) -> None:
    path = tmp_path / "console.json"
    path.write_text(
        json.dumps(
            {
                "templates": ["broken", {"id": "ok", "text": "Ready"}],
                "rules": [
                    42,
                    {
                        "id": "valid",
                        "match_prefix": "ALERT:",
                        "device_ids": ["X3-ONE"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    store = MeshtasticConsoleStore(path)

    assert [item["id"] for item in store.payload()["templates"]] == ["ok"]
    assert [item["id"] for item in store.payload()["rules"]] == ["valid"]
    with pytest.raises(
        MeshtasticConsoleValidationError, match="must start with notify"
    ):
        store.replace(
            {
                "templates": [],
                "rules": [
                    {
                        "id": "unsafe-notify",
                        "match_prefix": "ALERT:",
                        "device_ids": ["X3-ONE"],
                        "notify": True,
                        "notify_service": "script.untrusted",
                    }
                ],
            }
        )


def test_flexhub_summary_requires_bridge_auth_and_health_is_redacted(
    tmp_path: Path,
) -> None:
    app = create_app(
        BridgeConfig(state_path=tmp_path / "state.json", api_key="studio-secret")
    )
    app.state.flexhub.configure("http://10.200.40.55")
    app.state.flexhub._meshtastic_console["last_message"] = {
        "text": "Private mesh message"
    }
    client = TestClient(app)

    assert client.get("/api/v1/flexhub").status_code == 401
    authorized = client.get(
        "/api/v1/flexhub",
        headers={"X-FlexDisplay-Bridge-Key": "studio-secret"},
    )
    assert authorized.status_code == 200
    assert (
        authorized.json()["meshtastic_console"]["last_message"]["text"]
        == "Private mesh message"
    )
    health = client.get("/healthz").json()["flexhub"]
    assert "meshtastic_console" not in health
    assert "url" not in health


def test_bridge_proxies_console_actions_and_routes_alert_to_display(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_calls: list[str] = []
    notifications: list[tuple[str, dict[str, Any]]] = []
    message_fetches = 0

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        nonlocal message_fetches
        if url.endswith("/api/flexhub/status"):
            return FakeResponse({"state": "ready", "network": {"ip": "10.200.40.55"}})
        if url.endswith("/api/flexhub/meshtastic/nodes"):
            return FakeResponse({"nodes": [{"id": "!12345678", "name": "Showroom"}]})
        message_fetches += 1
        return FakeResponse(
            {
                "messages": [
                    {
                        "sequence": 40 + message_fetches,
                        "direction": "inbound",
                        "sender": "Showroom",
                        "channel": 0,
                        "text": "ALERT: Cooling fault",
                    }
                ],
                "next_after": 42,
            }
        )

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        post_calls.append(url)
        if url.endswith("/meshtastic/messages"):
            return FakeResponse({"accepted": True, "packet_id": 99})
        return FakeResponse({"accepted": True, "state": "scanning"})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(
        HomeAssistantClient,
        "call_service",
        lambda self, service, entity_id="", data=None: (
            notifications.append((service, dict(data or {}))) is None,
            f"called {service}",
        ),
    )
    app = create_app(BridgeConfig(state_path=tmp_path / "state.json"))
    client = TestClient(app)
    checked_in = client.get(
        "/api/v1/screen",
        headers={
            "X-FlexDisplay-ID": "X3-ONE",
            "X-FlexDisplay-Width": "528",
            "X-FlexDisplay-Height": "792",
            "X-FlexDisplay-Model": "XTEINK_X3",
        },
    )
    assert checked_in.status_code == 200
    assert (
        client.put(
            "/api/v1/flexhub/settings",
            json={"url": "http://10.200.40.55", "access_pin": "2468"},
        ).status_code
        == 200
    )
    baseline = client.get("/api/v1/flexhub/meshtastic/messages?after=0&limit=20")
    assert baseline.json()["bridge"]["new_messages"] == 0
    saved = client.put(
        "/api/v1/flexhub/meshtastic/settings",
        json={
            "templates": [{"id": "ok", "label": "OK", "text": "All clear"}],
            "rules": [
                {
                    "id": "alert",
                    "label": "Alert display",
                    "match_prefix": "ALERT:",
                    "strip_prefix": True,
                    "device_ids": ["X3-ONE"],
                    "priority": "critical",
                    "notify": True,
                    "notify_service": "notify.mobile_app_test",
                }
            ],
        },
    )
    assert saved.status_code == 200

    messages = client.get("/api/v1/flexhub/meshtastic/messages?after=0&limit=20")
    assert messages.status_code == 200
    assert messages.json()["bridge"]["new_messages"] == 1
    record = app.state.store.get("X3-ONE")
    assert record["screen_override_id"]
    assert "refresh" in record["pending_commands"]
    evaluation = app.state.meshtastic_console.payload()["last_evaluation"]
    assert all(result["success"] for result in evaluation["results"])
    assert notifications == [
        (
            "notify.mobile_app_test",
            {"title": "MESHTASTIC ALERT", "message": "Cooling fault"},
        )
    ]

    assert (
        client.get("/api/v1/flexhub/meshtastic/nodes").json()["nodes"][0]["name"]
        == "Showroom"
    )
    sent = client.post(
        "/api/v1/flexhub/meshtastic/messages",
        json={"text": "Hello mesh", "destination": "broadcast", "channel": 0},
    )
    assert sent.json()["packet_id"] == 99
    assert client.post("/api/v1/flexhub/actions/scan").status_code == 200
    assert client.post("/api/v1/flexhub/actions/deliver").status_code == 200
    assert any(url.endswith("/api/flexhub/scan") for url in post_calls)
    assert any(url.endswith("/api/flexhub/send") for url in post_calls)
    assert (
        client.post("/api/v1/flexhub/meshtastic/read").json()["meshtastic_console"][
            "unread_count"
        ]
        == 0
    )


def test_successful_send_publishes_once_after_hub_record_is_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[dict[str, Any]] = []
    sent = False

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        nonlocal sent
        sent = True
        return FakeResponse({"accepted": True, "sequence": 7, "packet_id": 99})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(
        requests,
        "get",
        lambda *args, **kwargs: FakeResponse(
            {
                "session_id": 55,
                "messages": (
                    [
                        {
                            "session_id": 55,
                            "sequence": 7,
                            "packet_id": 99,
                            "direction": "outbound",
                            "delivery_state": "submitted",
                            "text": "Hello mesh",
                        }
                    ]
                    if sent
                    else []
                ),
                "next_after": 7 if sent else 0,
            }
        ),
    )
    monkeypatch.setattr(
        MqttService,
        "publish_flexhub_message",
        lambda self, message: published.append(dict(message)),
    )
    app = create_app(BridgeConfig(state_path=tmp_path / "state.json"))
    app.state.flexhub.configure("http://10.200.40.55")
    client = TestClient(app)

    assert client.get("/api/v1/flexhub/meshtastic/messages").status_code == 200
    assert published == []
    assert (
        client.post(
            "/api/v1/flexhub/meshtastic/messages",
            json={"text": "Hello mesh", "destination": "broadcast", "channel": 0},
        ).status_code
        == 200
    )
    assert published == []
    assert client.get("/api/v1/flexhub/meshtastic/messages").status_code == 200
    assert [(item["sequence"], item["packet_id"]) for item in published] == [(7, 99)]
