from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient
from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import BridgeConfig, MqttConfig
from flexdisplay_bridge.flexhub_client import FlexHubClient, FlexHubClientError
from flexdisplay_bridge.mqtt_service import MqttService


class FakeResponse:
    def __init__(
        self,
        payload: dict | None = None,
        *,
        status_code: int = 200,
        content_type: str = "application/json",
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.text = text if text is not None else json.dumps(payload or {})

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> dict:
        if self.payload is None:
            raise ValueError("No JSON response")
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
                {
                    "device_id": "X4-TWO",
                    "online": False,
                    "policy_sync_state": "pending",
                },
            ],
        },
    }


def test_flexhub_client_persists_configuration_and_polls(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, dict, float, bool]] = []

    def fake_get(
        url: str, *, headers: dict, timeout: float, allow_redirects: bool
    ) -> FakeResponse:
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
    restored = FlexHubClient(path).summary()
    assert restored["access_pin_configured"] is True
    assert restored["configuration_source"] == "bridge_saved"
    assert restored["saved_configuration"] is True


def test_flexhub_client_does_not_restore_bridge_default_after_saved_disconnect(
    tmp_path: Path,
) -> None:
    path = tmp_path / "flexhub.json"
    client = FlexHubClient(
        path,
        default_url="http://home-assistant-option.test",
        default_access_pin="default-pin",
    )
    assert client.summary()["configuration_source"] == "bridge_configuration"

    client.configure("", "")
    restored = FlexHubClient(
        path,
        default_url="http://home-assistant-option.test",
        default_access_pin="default-pin",
    ).summary()

    assert restored["configured"] is False
    assert restored["access_pin_configured"] is False
    assert restored["configuration_source"] == "bridge_saved"
    assert restored["saved_configuration"] is True


@pytest.mark.parametrize(
    ("saved", "expected_url", "pin_configured"),
    [
        ({"url": "http://saved.test"}, "http://saved.test", True),
        ({"access_pin": "saved-pin"}, "http://default.test", True),
    ],
)
def test_flexhub_partial_legacy_state_merges_missing_default_field(
    tmp_path: Path,
    saved: dict[str, str],
    expected_url: str,
    pin_configured: bool,
) -> None:
    path = tmp_path / "flexhub.json"
    path.write_text(json.dumps(saved), encoding="utf-8")

    summary = FlexHubClient(
        path,
        default_url="http://default.test",
        default_access_pin="default-pin",
    ).summary()

    assert summary["url"] == expected_url
    assert summary["access_pin_configured"] is pin_configured
    assert summary["configuration_source"] == "bridge_saved"


@pytest.mark.parametrize(
    "saved",
    [{"url": ""}, {"access_pin": ""}],
)
def test_flexhub_empty_partial_legacy_state_preserves_default_field(
    tmp_path: Path, saved: dict[str, str]
) -> None:
    path = tmp_path / "flexhub.json"
    path.write_text(json.dumps(saved), encoding="utf-8")

    summary = FlexHubClient(
        path,
        default_url="http://default.test",
        default_access_pin="default-pin",
    ).summary()

    assert summary["url"] == "http://default.test"
    assert summary["access_pin_configured"] is True
    assert summary["saved_url_authoritative"] is False
    assert summary["saved_pin_authoritative"] is False


@pytest.mark.parametrize(
    "url",
    [
        "http://hub.example:bad",
        "http://hub.example:70000",
        "http://[malformed-ipv6",
    ],
)
def test_flexhub_rejects_invalid_ports(tmp_path: Path, url: str) -> None:
    with pytest.raises(FlexHubClientError, match="invalid port|malformed"):
        FlexHubClient(tmp_path / "flexhub.json").configure(url)


@pytest.mark.parametrize("pin", ["SECRET\nTOKEN", "PIN\U0001f642", "A" * 65])
def test_flexhub_rejects_unsafe_header_pins_without_persisting(
    tmp_path: Path, pin: str
) -> None:
    path = tmp_path / "flexhub.json"
    client = FlexHubClient(path)

    with pytest.raises(FlexHubClientError, match="visible ASCII") as error:
        client.configure("http://hub.test", pin)

    assert pin not in str(error.value)
    assert not path.exists()


def test_flexhub_request_errors_never_republish_access_pin(
    tmp_path: Path, monkeypatch
) -> None:
    secret = "LEAK-SENTINEL-PIN"

    def fail_request(*args, **kwargs):
        raise requests.exceptions.InvalidHeader(f"invalid header {secret}")

    monkeypatch.setattr(requests, "get", fail_request)
    client = FlexHubClient(tmp_path / "flexhub.json")
    client.configure("http://hub.test", secret)

    summary = client.poll()

    assert summary["error"] == "FlexHub request configuration is invalid"
    assert secret not in json.dumps(summary)
    service = MqttService(MqttConfig(enabled=True), lambda *args: None)

    class FakeMqttClient:
        def __init__(self) -> None:
            self.messages: list[tuple[str, object, bool]] = []

        def publish(self, topic: str, payload: object, retain: bool = False) -> None:
            self.messages.append((topic, payload, retain))

    mqtt_client = FakeMqttClient()
    service.client = mqtt_client
    service.connected = True
    service.publish_flexhub(summary)
    assert secret not in json.dumps(mqtt_client.messages)


def test_flexhub_remote_error_body_never_reflects_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    secret = "LEAK-SENTINEL-PIN"
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            {"error": f"bad token {secret}"}, status_code=409
        ),
    )
    client = FlexHubClient(tmp_path / "flexhub.json")
    client.configure("http://hub.test", "safe-pin")

    with pytest.raises(FlexHubClientError) as error:
        client.action("scan")

    assert str(error.value) == "FlexHub API request failed (HTTP 409)"
    assert secret not in str(error.value)


def test_flexhub_settings_rejects_unsafe_pin_with_http_400(tmp_path: Path) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json")

    with TestClient(create_app(config)) as client:
        response = client.put(
            "/api/v1/flexhub/settings",
            json={"url": "http://hub.test", "access_pin": "SECRET\nTOKEN"},
        )

    assert response.status_code == 400
    assert "SECRET" not in response.text


@pytest.mark.parametrize(
    "entered",
    [
        "http://10.200.40.55",
        "http://10.200.40.55/",
        "http://10.200.40.55/flexhub",
        "http://10.200.40.55/flexhub/",
        "http://10.200.40.55/meshtastic",
        "http://10.200.40.55/api/flexhub/status",
    ],
)
def test_flexhub_client_normalizes_known_hub_routes(
    tmp_path: Path, entered: str
) -> None:
    client = FlexHubClient(tmp_path / "flexhub.json")
    summary = client.configure(entered)

    assert summary["url"] == "http://10.200.40.55"
    assert summary["selector_url"] == "http://10.200.40.55/"
    assert summary["console_url"] == "http://10.200.40.55/flexhub"
    assert summary["meshtastic_url"] == "http://10.200.40.55/meshtastic"
    assert summary["status_url"] == "http://10.200.40.55/api/flexhub/status"


def test_flexhub_client_rejects_unknown_page_paths(tmp_path: Path) -> None:
    client = FlexHubClient(tmp_path / "flexhub.json")

    with pytest.raises(ValueError, match="hub base address"):
        client.configure("http://10.200.40.55/admin")


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            FakeResponse(
                None,
                content_type="text/html",
                text="<!doctype html><title>Meshtastic</title>",
            ),
            "Meshtastic web page was reached",
        ),
        (
            FakeResponse(None, status_code=404, text="Not found"),
            "FlexHub status API was not found (HTTP 404)",
        ),
        (
            FakeResponse(
                None,
                status_code=302,
                headers={"Location": "http://meshtastic.local/"},
            ),
            "FlexHub status endpoint redirected",
        ),
    ],
)
def test_flexhub_client_reports_route_compatibility_errors(
    tmp_path: Path,
    monkeypatch,
    response: FakeResponse,
    expected: str,
) -> None:
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    client = FlexHubClient(tmp_path / "flexhub.json")
    client.configure("http://10.200.40.55")

    summary = client.poll()

    assert summary["connected"] is False
    assert expected in summary["error"]
    assert summary["status_url"] == "http://10.200.40.55/api/flexhub/status"


def test_studio_exposes_fleet_policy_and_flexhub_workspaces(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        requests, "get", lambda *args, **kwargs: FakeResponse(_hub_status())
    )
    config = BridgeConfig(state_path=tmp_path / "state.json")
    with TestClient(create_app(config)) as client:
        html = client.get("/studio/").text
        assert 'id="openFleetPolicies"' in html
        assert 'id="fleetPolicyMode"' in html
        assert 'id="fleetPolicyScope"' in html
        assert '<option value="devices">Selected devices</option>' in html
        assert 'id="openFlexHub"' in html
        assert 'id="testFlexHub"' in html
        assert 'id="openFlexHubSelector"' in html
        assert 'id="openFlexHubConsole"' in html
        assert 'id="openMeshtasticConsole"' in html
        assert "Enter the hub address without a page path" in html
        assert "Meshtastic status" in html

        connected = client.put(
            "/api/v1/flexhub/settings",
            json={"url": "http://10.200.40.55", "access_pin": "1234"},
        )
        assert connected.status_code == 200
        assert connected.json()["status"]["network"]["ip"] == "10.200.40.55"
        assert connected.json()["console_url"] == "http://10.200.40.55/flexhub"
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
    assert (
        "homeassistant/binary_sensor/flexhub/meshtastic_mqtt_connected/config" in topics
    )
