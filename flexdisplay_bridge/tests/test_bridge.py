from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import BridgeConfig, DeviceConfig, HomeAssistantConfig
from flexdisplay_bridge.home_assistant import EntityState
from flexdisplay_bridge.renderer import DashboardRenderer, _icon_kind


def test_screen_registers_device_and_returns_x4_png(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
        devices={"X4-DEMO01": DeviceConfig(name="Test X4", model="X4", width=480, height=800)},
    )
    with TestClient(create_app(config)) as client:
        response = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-DEMO01",
                "X-FlexDisplay-Width": "480",
                "X-FlexDisplay-Height": "800",
                "X-FlexDisplay-Battery-Percent": "76",
                "X-FlexDisplay-RSSI": "-54",
            },
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-flexdisplay-refresh-interval"] == "900"
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (480, 800)
            assert image.mode == "1"

        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["battery_percent"] == 76
        assert record["rssi"] == -54


def test_refresh_command_is_queued_then_consumed(tmp_path: Path) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json", api_key="secret")
    with TestClient(create_app(config)) as client:
        unauthorized = client.post("/api/v1/devices/X4-DEMO01/commands/refresh")
        assert unauthorized.status_code == 401
        queued = client.post(
            "/api/v1/devices/X4-DEMO01/commands/refresh",
            headers={"X-FlexDisplay-Bridge-Key": "secret"},
        )
        assert queued.status_code == 200
        assert queued.json()["queued"] == "refresh"

        screen = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        assert screen.headers["x-flexdisplay-commands"] == "refresh"
        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["pending_commands"] == []
        assert record["render_revision"] == 1


def test_invalid_device_id_is_rejected(tmp_path: Path) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json")
    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "../../bad"})
        assert response.status_code == 400


def test_visual_dashboard_renders_eight_large_cards() -> None:
    entities = [
        EntityState("sensor.inside_temperature", "Inside Temperature", "22.0", "°C", True),
        EntityState("sensor.movie_humidity", "Movie Room Humidity", "40", "%", True),
        EntityState("sensor.home_battery", "Home Battery", "83", "%", True),
        EntityState("sensor.site_power", "Site Power", "6.5", "kW", True),
        EntityState("sensor.solar_power", "Solar Power", "1.0", "kW", True),
        EntityState("sensor.ac70_battery", "AC70 Battery", "100", "%", True),
        EntityState("sensor.extra_temperature", "Garage Temperature", "19.8", "°C", True),
        EntityState("sensor.extra_power", "Load Power", "2.7", "kW", True),
    ]
    content = DashboardRenderer().render(
        title="HOME ASSISTANT",
        device={"device_id": "X4-DEMO01", "battery_percent": 76, "rssi": -54},
        width=480,
        height=800,
        entities=entities,
    )
    with Image.open(io.BytesIO(content)) as image:
        assert image.size == (480, 800)
        assert image.mode == "1"
        assert image.getbbox() is not None
        assert image.getbbox()[2:] == (480, 800)


def test_entity_icons_are_selected_from_semantics() -> None:
    assert _icon_kind(EntityState("sensor.room_temperature", "Room", "22", "°C", True)) == "temperature"
    assert _icon_kind(EntityState("sensor.room_humidity", "Room", "40", "%", True)) == "humidity"
    assert _icon_kind(EntityState("sensor.solar_power", "Solar", "1.2", "kW", True)) == "solar"
    assert _icon_kind(EntityState("sensor.home_battery", "Battery", "80", "%", True)) == "battery"
    assert _icon_kind(EntityState("sensor.site_power", "Site", "4.2", "kW", True)) == "power"
