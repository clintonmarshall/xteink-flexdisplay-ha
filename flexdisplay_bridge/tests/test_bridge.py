from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from flexdisplay_bridge.app import _sleep_plan, create_app
from flexdisplay_bridge.config import (
    BridgeConfig,
    DashboardPageConfig,
    DashboardProfileConfig,
    DeviceConfig,
    EntityConfig,
    FirmwareConfig,
    HomeAssistantConfig,
)
from flexdisplay_bridge.dashboards import build_dashboard_pages
from flexdisplay_bridge.home_assistant import EntityState
from flexdisplay_bridge.renderer import DashboardRenderer, _icon_kind
from PIL import Image


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
        assert response.headers["x-flexdisplay-provisioned"] == "true"
        assert response.headers["x-flexdisplay-device-name"] == "Test X4"
        assert response.headers["x-flexdisplay-assigned-mode"] == "home_assistant"
        assert response.headers["x-flexdisplay-auto-start"] == "true"
        assert response.headers["x-flexdisplay-sleep-action"] == "scheduled"
        assert int(response.headers["x-flexdisplay-sleep-seconds"]) >= 60
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (480, 800)
            assert image.mode == "1"

        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["battery_percent"] == 76
        assert record["rssi"] == -54
        assert record["provisioned"] is True
        assert record["assigned_name"] == "Test X4"


def test_unknown_device_is_zero_touch_provisioned_with_defaults(tmp_path: Path) -> None:
    profile = DashboardProfileConfig(name="wall")
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        profiles={"wall": profile},
        default_profile="wall",
    )
    with TestClient(create_app(config)) as client:
        screen = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-NEW001",
                "X-FlexDisplay-Model": "XTEINK_X3",
                "X-FlexDisplay-Width": "528",
                "X-FlexDisplay-Height": "792",
            },
        )
        assert screen.status_code == 200
        assert screen.headers["x-flexdisplay-profile"] == "wall"
        assert screen.headers["x-flexdisplay-assigned-mode"] == "home_assistant"
        record = client.get("/api/v1/devices/X3-NEW001").json()
        assert record["name"] == "X3-NEW001"
        assert record["assigned_profile"] == "wall"
        assert record["assigned_auto_start"] is True
        assert record["assigned_intelligent_sleep"] is True
        assert record["available_profiles"] == ["wall"]


def test_authenticated_provisioning_updates_device_policy(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="secret",
        profiles={
            "default": DashboardProfileConfig(name="default"),
            "showroom": DashboardProfileConfig(name="showroom"),
        },
    )
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        unauthorized = client.put(
            "/api/v1/devices/X4-DEMO01/provision",
            json={"profile": "showroom"},
        )
        assert unauthorized.status_code == 401

        provisioned = client.put(
            "/api/v1/devices/X4-DEMO01/provision",
            headers={"X-FlexDisplay-Bridge-Key": "secret"},
            json={
                "name": "Showroom Panel",
                "area": "Showroom",
                "profile": "showroom",
                "mode": "home_assistant",
                "refresh_interval_seconds": 300,
                "intelligent_sleep": True,
                "active_start": "07:00",
                "active_end": "21:30",
                "timezone": "Australia/Melbourne",
                "low_battery_percent": 40,
            },
        )
        assert provisioned.status_code == 200
        device = provisioned.json()["device"]
        assert device["name"] == "Showroom Panel"
        assert device["area"] == "Showroom"
        assert device["assigned_profile"] == "showroom"
        assert device["assigned_refresh_interval_seconds"] == 300
        assert device["assigned_active_start"] == "07:00"
        assert device["assigned_active_end"] == "21:30"
        assert device["assigned_low_battery_percent"] == 40

        screen = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        assert screen.headers["x-flexdisplay-device-name"] == "Showroom Panel"
        assert screen.headers["x-flexdisplay-area"] == "Showroom"
        assert screen.headers["x-flexdisplay-profile"] == "showroom"
        assert screen.headers["x-flexdisplay-refresh-interval"] == "300"


def test_sleep_plan_respects_usb_active_hours_battery_and_unchanged_images() -> None:
    profile = DeviceConfig(
        name="Test",
        refresh_interval_seconds=300,
        intelligent_sleep=True,
        active_start="06:00",
        active_end="22:00",
        timezone="Australia/Melbourne",
        critical_battery_percent=15,
        low_battery_percent=35,
        low_battery_multiplier=4,
        unchanged_image_multiplier=2,
        stay_awake_on_usb=True,
    )
    active = datetime(2026, 7, 25, 2, 0, tzinfo=UTC)  # 12:00 Melbourne
    inactive = datetime(2026, 7, 25, 14, 0, tzinfo=UTC)  # 00:00 Melbourne

    assert _sleep_plan(profile, 80, True, False, active)["sleep_action"] == "awake"
    assert _sleep_plan(profile, 10, False, False, active)["sleep_action"] == "power_off"
    assert _sleep_plan(profile, 80, False, False, active)["sleep_seconds"] == 300
    assert _sleep_plan(profile, 30, False, False, active)["sleep_seconds"] == 1200
    assert _sleep_plan(profile, 80, False, True, active)["sleep_seconds"] == 600

    overnight = _sleep_plan(profile, 80, False, False, inactive)
    assert overnight["sleep_action"] == "scheduled"
    assert overnight["sleep_reason"] == "outside_active_hours"
    assert overnight["sleep_seconds"] == 21600


def test_screen_marks_matching_image_as_unchanged(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
        devices={
            "X3-DEMO01": DeviceConfig(
                name="Test X3",
                refresh_interval_seconds=300,
                unchanged_image_multiplier=3,
            )
        },
    )
    with TestClient(create_app(config)) as client:
        first = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X3-DEMO01"})
        digest = first.headers["x-flexdisplay-image-sha256"]
        second = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-DEMO01",
                "X-FlexDisplay-Image-SHA256": digest,
            },
        )
        assert second.headers["x-flexdisplay-image-unchanged"] == "true"
        assert second.headers["x-flexdisplay-sleep-reason"] in {
            "unchanged_image",
            "low_battery_unchanged",
            "outside_active_hours",
        }


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
        assert record["dispatched_commands"] == ["refresh"]

        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-DEMO01",
                "X-FlexDisplay-Command-Result": "refresh:complete",
            },
        )
        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["last_command_result"] == "refresh:complete"
        assert record["dispatched_commands"] == []


def test_next_command_advances_readable_dashboard_pages(tmp_path: Path) -> None:
    entities = (
        EntityConfig("sensor.inside_temperature", "Inside Temperature", "°C"),
        EntityConfig("sensor.movie_temperature", "Movie Room Temperature", "°C"),
        EntityConfig("sensor.movie_humidity", "Movie Room Humidity", "%"),
        EntityConfig("sensor.home_battery", "Home Battery", "%"),
        EntityConfig("sensor.site_power", "Site Power", "kW"),
        EntityConfig("sensor.solar_power", "Solar Power", "kW"),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
        devices={"X4-DEMO01": DeviceConfig(name="Test X4", entities=entities)},
    )
    with TestClient(create_app(config)) as client:
        first = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        assert first.headers["x-flexdisplay-page"] == "1"
        assert first.headers["x-flexdisplay-page-count"] == "8"
        assert first.headers["x-flexdisplay-page-title"] == "OVERVIEW"

        client.post("/api/v1/devices/X4-DEMO01/commands/next")
        second = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        assert second.headers["x-flexdisplay-commands"] == "next"
        assert second.headers["x-flexdisplay-page"] == "2"
        assert second.headers["x-flexdisplay-page-title"] == "TEMPERATURES"
        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["dashboard_page_title"] == "TEMPERATURES"
        assert record["dashboard_page_number"] == 2
        assert record["dashboard_page_count"] == 8


def test_profile_navigation_and_page_selection(tmp_path: Path) -> None:
    entities = (
        EntityConfig("sensor.room_temperature", "Room", "°C"),
        EntityConfig("sensor.solar_power", "Solar", "kW"),
    )
    profile = DashboardProfileConfig(
        name="wall",
        pages=(
            DashboardPageConfig("CLIMATE", (entities[0],)),
            DashboardPageConfig("ENERGY", (entities[1],)),
        ),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
        profiles={"wall": profile},
        devices={
            "X4-DEMO01": DeviceConfig(
                name="Test X4",
                entities=entities,
                profile="wall",
            )
        },
    )
    with TestClient(create_app(config)) as client:
        first = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        assert first.headers["x-flexdisplay-page-title"] == "CLIMATE"
        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["dashboard_pages"] == ["CLIMATE", "ENERGY"]
        assert record["dashboard_profile"] == "wall"

        client.post("/api/v1/devices/X4-DEMO01/commands/previous")
        previous = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        assert previous.headers["x-flexdisplay-page-title"] == "ENERGY"

        client.post("/api/v1/devices/X4-DEMO01/commands/overview")
        overview = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        assert overview.headers["x-flexdisplay-page-title"] == "CLIMATE"

        client.post("/api/v1/devices/X4-DEMO01/commands/page-2")
        selected = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        assert selected.headers["x-flexdisplay-page-title"] == "ENERGY"


def test_button_events_and_extended_telemetry_are_recorded(tmp_path: Path) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json")
    with TestClient(create_app(config)) as client:
        response = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-DEMO01",
                "X-FlexDisplay-USB-Connected": "true",
                "X-FlexDisplay-Uptime-Seconds": "123",
                "X-FlexDisplay-Free-Heap": "118936",
                "X-FlexDisplay-Min-Free-Heap": "110000",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-Wake-Reason": "power_button",
                "X-FlexDisplay-Button-Events": "1,left,pressed,120000;2,confirm,pressed,121000",
            },
        )
        assert response.status_code == 200
        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["usb_connected"] is True
        assert record["uptime_seconds"] == 123
        assert record["free_heap"] == 118936
        assert record["sd_ready"] is True
        assert record["wake_reason"] == "power_button"
        assert record["last_button"] == "confirm"
        assert record["button_press_count"] == 2
        assert len(record["recent_button_events"]) == 2

        # A retried check-in must not double count buffered events.
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-DEMO01",
                "X-FlexDisplay-Button-Events": "1,left,pressed,120000;2,confirm,pressed,121000",
            },
        )
        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["button_press_count"] == 2
        assert len(client.get("/api/v1/devices/X4-DEMO01/events").json()["events"]) == 2


def test_install_command_delivers_release_metadata(tmp_path: Path) -> None:
    firmware = FirmwareConfig(
        version="0.6.0",
        url="https://example.test/firmware.bin",
        sha256="ab" * 32,
        size=5_500_000,
        minimum_battery_percent=45,
    )
    config = BridgeConfig(state_path=tmp_path / "state.json", firmware=firmware)
    with TestClient(create_app(config)) as client:
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-DEMO01",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.5.0",
            },
        )
        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["latest_firmware"] == "0.6.0"
        assert record["update_available"] is True

        queued = client.post("/api/v1/devices/X4-DEMO01/commands/install")
        assert queued.status_code == 200
        screen = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        assert screen.headers["x-flexdisplay-commands"] == "install"
        assert screen.headers["x-flexdisplay-latest-firmware"] == "0.6.0"
        assert screen.headers["x-flexdisplay-firmware-url"] == firmware.url
        assert screen.headers["x-flexdisplay-firmware-sha256"] == firmware.sha256
        assert screen.headers["x-flexdisplay-firmware-size"] == str(firmware.size)
        assert screen.headers["x-flexdisplay-firmware-min-battery"] == "45"


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
    assert _icon_kind(EntityState("device.wifi", "Wi-Fi Signal", "-60", "dBm", True)) == "wifi"
    assert _icon_kind(EntityState("device.storage", "SD Card", "Ready", "", True)) == "storage"
    assert _icon_kind(EntityState("device.uptime", "Uptime", "2 h", "", True)) == "clock"


def test_dashboard_pages_group_home_assistant_values() -> None:
    entities = [
        EntityState("sensor.inside_temperature", "Inside Temperature", "22", "°C", True),
        EntityState("sensor.movie_humidity", "Movie Room Humidity", "40", "%", True),
        EntityState("sensor.home_battery", "Home Battery", "83", "%", True),
        EntityState("sensor.site_power", "Site Power", "6.5", "kW", True),
        EntityState("sensor.solar_power", "Solar Power", "1.0", "kW", True),
    ]
    pages = build_dashboard_pages(
        entities,
        {"battery_percent": 76, "rssi": -54, "uptime_seconds": 3600, "sd_ready": True},
    )
    assert [page.title for page in pages] == [
        "OVERVIEW",
        "TEMPERATURES",
        "HUMIDITY",
        "BATTERIES",
        "POWER",
        "ENERGY",
        "DEVICE HEALTH",
        "CONNECTIVITY",
    ]
    assert all(len(page.entities) <= 4 for page in pages)
