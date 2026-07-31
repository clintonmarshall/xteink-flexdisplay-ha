from __future__ import annotations

import json
from pathlib import Path

import requests
from fastapi.testclient import TestClient

from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import BridgeConfig, MqttConfig
from flexdisplay_bridge.flexhub_client import FlexHubClient
from flexdisplay_bridge.mqtt_service import MqttService


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def _hub_status() -> dict:
    return {
        "state": "ready",
        "detail": "Ready to send",
        "firmware": "2.8.0",
        "platform_version": "0.33.0",
        "target_count": 8,
        "delivered": 7,
        "failed": 1,
        "network": {
            "wifi_connected": True,
            "ip": "10.200.40.55",
            "rssi": -54,
            "free_heap": 123456,
            "uptime_seconds": 3600,
        },
        "meshtastic": {
            "node_id": "!12345678",
            "firmware": "2.8.0",
            "node_count": 4,
            "online_node_count": 3,
            "mqtt_enabled": True,
            "mqtt_connected": True,
        },
        "storage": {"ready": True, "free_bytes": 28_000_000_000},
        "fleet": {
            "connected": True,
            "selected_policy": "balanced",
            "selected_scope": "all",
            "devices": [
                {"device_id": "X3-ONE", "online": True},
                {"device_id": "X4-TWO", "online": False, "policy_sync_state": "pending"},
            ],
        },
    }


def test_flexhub_client_persists_configuration_and_polls(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, dict, float, bool]] = []

    def fake_get(url: str, *, headers: dict, timeout: float, allow_redirects: bool) -> FakeResponse:
        calls.append((url, headers, timeout, allow_redirects))
        return FakeResponse(_hub_status())

    monkeypatch.setattr(requests, "get", fake_get)
    path = tmp_path / "flexhub.json"
    client = FlexHubClient(path)
    client.configure("http://10.200.40.55/", "1234")
    result = client.poll()

    assert result["connected"] is True
    assert result["status"]["meshtastic"]["mqtt_connected"] is True
    assert calls == [
        (
            "http://10.200.40.55/api/flexhub/status",
            {"Accept": "application/json", "X-FlexHub-Token": "1234"},
            5.0,
            False,
        )
    ]
    assert json.loads(path.read_text(encoding="utf-8"))["url"] == "http://10.200.40.55"
    assert FlexHubClient(path).summary()["access_pin_configured"] is True


def test_studio_exposes_fleet_policy_and_flexhub_workspaces(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: FakeResponse(_hub_status()))
    config = BridgeConfig(state_path=tmp_path / "state.json")
    with TestClient(create_app(config)) as client:
        html = client.get("/studio/").text
        assert 'id="openFleetPolicies"' in html
        assert 'id="fleetPolicyMode"' in html
        assert 'id="fleetPolicyScope"' in html
        assert '<option value="devices">Selected devices</option>' in html
        assert 'id="openFlexHub"' in html
        assert "Meshtastic status" in html

        connected = client.put(
            "/api/v1/flexhub/settings",
            json={"url": "http://10.200.40.55", "access_pin": "1234"},
        )
        assert connected.status_code == 200
        assert connected.json()["status"]["network"]["ip"] == "10.200.40.55"
        assert client.get("/api/v1/flexhub").json()["connected"] is True


def test_mqtt_discovery_publishes_flexhub_health_and_meshtastic() -> None:
    class FakeMqttClient:
        def __init__(self) -> None:
            self.messages: list[tuple[str, object, bool]] = []

        def publish(self, topic: str, payload: object, retain: bool = False) -> None:
            self.messages.append((topic, payload, retain))

    service = MqttService(MqttConfig(enabled=True), lambda *args: None)
    service.client = FakeMqttClient()
    service.connected = True
    service.publish_flexhub(
        {
            "connected": True,
            "url": "http://10.200.40.55",
            "last_seen": "2026-07-31T12:00:00+00:00",
            "error": "",
            "status": _hub_status(),
        }
    )

    topics = {topic: payload for topic, payload, _ in service.client.messages}
    assert topics["flexdisplay/flexhub/availability"] == "online"
    state = json.loads(topics["flexdisplay/flexhub/state"])
    assert state["fleet_devices"] == 2
    assert state["fleet_policy_pending"] == 1
    assert state["meshtastic_mqtt_connected"] is True
    assert "homeassistant/sensor/flexhub/meshtastic_nodes/config" in topics
    assert "homeassistant/binary_sensor/flexhub/meshtastic_mqtt_connected/config" in topics
