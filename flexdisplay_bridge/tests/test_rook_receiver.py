from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import (
    BridgeConfig,
    DashboardPageConfig,
    DashboardProfileConfig,
    DeviceConfig,
    EntityConfig,
    MqttConfig,
)
from flexdisplay_bridge.mqtt_service import MqttService
from flexdisplay_bridge.home_assistant import EntityState, HomeAssistantClient
from PIL import Image


def _config(tmp_path: Path) -> BridgeConfig:
    profile = DashboardProfileConfig(
        name="spot",
        pages=(
            DashboardPageConfig(
                title="HOUSE",
                entities=(
                    EntityConfig("static.temperature", "Inside", source="static", value="21.5", unit="°C"),
                    EntityConfig("static.power", "Solar", source="static", value="3.2", unit="kW"),
                ),
            ),
        ),
    )
    return BridgeConfig(
        state_path=tmp_path / "state.json",
        profiles={"spot": profile},
        default_profile="spot",
    )


def test_rook_screen_is_round_safe_colour_png(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        response = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "ROOK-TEST01",
                "X-FlexDisplay-Model": "ROOK",
                "X-FlexDisplay-Firmware": "android-0.1.0",
                "X-FlexDisplay-Width": "480",
                "X-FlexDisplay-Height": "480",
                "X-FlexDisplay-Capabilities": "android,color,touch,round-display,png,empty-unchanged",
                "X-FlexDisplay-SD-Ready": "false",
            },
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert "X-FlexDisplay-Latest-Firmware" not in response.headers
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (480, 480)
            assert image.mode == "RGB"
            assert image.getpixel((0, 0)) == (4, 10, 17)

        device = client.get("/api/v1/devices/ROOK-TEST01").json()
        assert device["model"] == "ROOK"
        assert device["display_shape"] == "round"
        assert device["touch_available"] is True
        assert device["color_available"] is True
        assert device["client_platform"] == "android"
        assert device["health_state"] == "healthy"
        assert device["health_issues"] == []
        assert device["consecutive_sd_failures"] == 0
        assert device["sd_failure_events"] == 0


def test_rook_cannot_receive_esp32_firmware(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "ROOK-TEST02",
                "X-FlexDisplay-Model": "ROOK",
                "X-FlexDisplay-Width": "480",
                "X-FlexDisplay-Height": "480",
                "X-FlexDisplay-Capabilities": "android,round-display",
            },
        )
        response = client.post("/api/v1/devices/ROOK-TEST02/commands/install")

        assert response.status_code == 409
        assert response.json()["detail"] == "No firmware release is configured"


def test_studio_has_echo_spot_preview(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        studio = client.get("/studio/")
        assert studio.status_code == 200
        assert 'data-model="ROOK"' in studio.text

        preview = client.post(
            "/api/v1/studio/preview",
            json={
                "model": "ROOK",
                "profile": {
                    "name": "spot-preview",
                    "pages": [
                        {
                            "title": "HOUSE",
                            "entities": [
                                {
                                    "entity_id": "static.state",
                                    "label": "Front door",
                                    "source": "static",
                                    "value": "Locked",
                                }
                            ],
                        }
                    ],
                },
            },
        )
        assert preview.status_code == 200
        with Image.open(io.BytesIO(preview.content)) as image:
            assert image.size == (480, 480)
            assert image.mode == "RGB"


def test_rook_mqtt_discovery_removes_embedded_firmware_update() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.messages: list[tuple[str, object, bool]] = []

        def publish(self, topic: str, payload: object, retain: bool = False) -> None:
            self.messages.append((topic, payload, retain))

    service = MqttService(
        MqttConfig(enabled=True, entity_source="mqtt"),
        lambda device_id, command, payload: None,
    )
    client = FakeClient()
    service.client = client
    service.connected = True
    state = {
        "device_id": "ROOK-MQTT01",
        "model": "ROOK",
        "firmware": "android-0.1.0",
        "available_profiles": ["home"],
        "available_modes": ["home_assistant"],
    }

    service.publish_device(
        "ROOK-MQTT01",
        DeviceConfig(name="Living room Spot", model="ROOK", width=480, height=480),
        state,
    )

    retained = {
        topic: payload
        for topic, payload, retain in client.messages
        if retain
    }
    assert retained["homeassistant/update/rook_mqtt01/firmware/config"] == ""
    assert retained["homeassistant/sensor/rook_mqtt01/sd_failure_events/config"] == ""
    assert retained["homeassistant/binary_sensor/rook_mqtt01/repeated_sd_failure/config"] == ""
    assert retained["homeassistant/binary_sensor/rook_mqtt01/sd_ready/config"] == ""
    assert "homeassistant/image/rook_mqtt01/current_screen/config" in retained


def test_rook_dashboard_interactions_are_paired_and_bounded(
    tmp_path: Path, monkeypatch
) -> None:
    profile = DashboardProfileConfig(
        name="touch",
        pages=(
            DashboardPageConfig(
                title="CONTROLS",
                entities=(
                    EntityConfig("light.porch", "Porch"),
                    EntityConfig("scene.goodnight", "Goodnight"),
                    EntityConfig("cover.garage_door", "Garage"),
                ),
            ),
        ),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        profiles={"touch": profile},
        default_profile="touch",
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        HomeAssistantClient,
        "fetch",
        lambda self, entities: (
            [
                EntityState(
                    entity.entity_id,
                    entity.label,
                    "closed" if entity.entity_id.startswith("cover.") else "off",
                    entity.unit,
                    True,
                )
                for entity in entities
            ],
            "",
        ),
    )
    monkeypatch.setattr(
        HomeAssistantClient,
        "call_service",
        lambda self, service, entity_id="", data=None: (
            calls.append((service, entity_id)) is None,
            f"called {service}",
        ),
    )
    receiver_headers = {
        "X-FlexDisplay-ID": "ROOK-TOUCH01",
        "X-FlexDisplay-Model": "ROOK",
        "X-FlexDisplay-Width": "480",
        "X-FlexDisplay-Height": "480",
        "X-FlexDisplay-Capabilities": "android,color,touch,round-display,png",
        "X-FlexDisplay-Receiver-Token": "paired-secret",
    }
    paired = {"X-FlexDisplay-Receiver-Token": "paired-secret"}
    with TestClient(create_app(config)) as client:
        assert client.get("/api/v1/screen", headers=receiver_headers).status_code == 200
        assert client.get("/api/v1/devices/ROOK-TOUCH01/interactions").status_code == 401
        response = client.get(
            "/api/v1/devices/ROOK-TOUCH01/interactions", headers=paired
        )
        assert response.status_code == 200
        interactions = response.json()["interactions"]
        assert [item["entity_id"] for item in interactions] == [
            "light.porch",
            "scene.goodnight",
            "cover.garage_door",
        ]
        assert interactions[0]["gesture"] == "tap"
        assert interactions[2]["gesture"] == "hold"
        assert interactions[0]["bounds"] == {
            "left": 58,
            "top": 118,
            "right": 235,
            "bottom": 257,
        }

        toggled = client.post(
            "/api/v1/devices/ROOK-TOUCH01/interactions/tile-1",
            headers=paired,
            json={},
        )
        assert toggled.status_code == 200
        assert calls[-1] == ("homeassistant.toggle", "light.porch")

        blocked = client.post(
            "/api/v1/devices/ROOK-TOUCH01/interactions/tile-3",
            headers=paired,
            json={},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["confirmation_required"] is True
        opened = client.post(
            "/api/v1/devices/ROOK-TOUCH01/interactions/tile-3",
            headers=paired,
            json={"confirmed": True},
        )
        assert opened.status_code == 200
        assert calls[-1] == ("cover.open_cover", "cover.garage_door")


def test_rook_notification_camera_chime_and_actions(
    tmp_path: Path, monkeypatch
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-secret",
        profiles={"spot": DashboardProfileConfig(name="spot")},
        default_profile="spot",
    )
    camera = io.BytesIO()
    Image.new("RGB", (320, 240), "navy").save(camera, format="JPEG")
    monkeypatch.setattr(
        HomeAssistantClient,
        "camera_image",
        lambda self, entity_id: (camera.getvalue(), "image/jpeg"),
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        HomeAssistantClient,
        "call_service",
        lambda self, service, entity_id="", data=None: (
            calls.append((service, entity_id)) is None,
            f"called {service}",
        ),
    )
    receiver_headers = {
        "X-FlexDisplay-ID": "ROOK-ALERT01",
        "X-FlexDisplay-Model": "ROOK",
        "X-FlexDisplay-Capabilities": "android,color,touch,round-display,png",
        "X-FlexDisplay-Receiver-Token": "alert-secret",
    }
    paired = {"X-FlexDisplay-Receiver-Token": "alert-secret"}
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers=receiver_headers)
        created = client.post(
            "/api/v1/devices/ROOK-ALERT01/notifications",
            headers=management,
            json={
                "title": "Front door",
                "message": "Someone rang the doorbell",
                "camera_entity": "camera.front_door",
                "chime": "doorbell",
                "duration": 20,
                "actions": [
                    {
                        "label": "Porch light",
                        "service": "light.turn_on",
                        "entity_id": "light.porch",
                    },
                    {
                        "label": "Open garage",
                        "service": "cover.open_cover",
                        "entity_id": "cover.garage_door",
                    },
                ],
            },
        )
        assert created.status_code == 200
        event = client.get(
            "/api/v1/devices/ROOK-ALERT01/notifications/next?after=0&timeout=0",
            headers=paired,
        ).json()
        notification = event["notification"]
        assert notification["title"] == "Front door"
        assert notification["has_image"] is True
        assert notification["chime"] == "doorbell"
        assert notification["actions"][1]["confirmation"] is True

        image = client.get(
            f"/api/v1/devices/ROOK-ALERT01/notifications/{notification['id']}/image",
            headers=paired,
        )
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/jpeg"

        action = client.post(
            f"/api/v1/devices/ROOK-ALERT01/notifications/{notification['id']}/actions/action-1",
            headers=paired,
            json={},
        )
        assert action.status_code == 200
        assert calls[-1] == ("light.turn_on", "light.porch")
        garage = client.post(
            f"/api/v1/devices/ROOK-ALERT01/notifications/{notification['id']}/actions/action-2",
            headers=paired,
            json={},
        )
        assert garage.status_code == 409

        dismissed = client.post(
            f"/api/v1/devices/ROOK-ALERT01/notifications/{notification['id']}/dismiss",
            headers=paired,
        )
        assert dismissed.json() == {"dismissed": True}


def test_rook_notification_rejects_arbitrary_home_assistant_service(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-secret",
        profiles={"spot": DashboardProfileConfig(name="spot")},
        default_profile="spot",
    )
    with TestClient(create_app(config)) as client:
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "ROOK-SAFE01",
                "X-FlexDisplay-Model": "ROOK",
                "X-FlexDisplay-Capabilities": "android,touch,round-display,png",
                "X-FlexDisplay-Receiver-Token": "safe-secret",
            },
        )
        response = client.post(
            "/api/v1/devices/ROOK-SAFE01/notifications",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
            json={
                "title": "Unsafe",
                "actions": [
                    {
                        "label": "Shell",
                        "service": "shell_command.anything",
                        "entity_id": "sensor.anything",
                    }
                ],
            },
        )
        assert response.status_code == 400

        alternate_target = client.post(
            "/api/v1/devices/ROOK-SAFE01/notifications",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
            json={
                "title": "Unsafe target",
                "actions": [
                    {
                        "label": "Porch",
                        "service": "light.turn_on",
                        "entity_id": "light.porch",
                        "data": {"target": {"entity_id": "light.everything"}},
                    }
                ],
            },
        )
        assert alternate_target.status_code == 400
