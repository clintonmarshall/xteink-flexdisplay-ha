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
