from __future__ import annotations

import io
import json
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
from flexdisplay_bridge.home_assistant import EntityState, HomeAssistantClient
from flexdisplay_bridge.renderer import DashboardRenderer, _icon_kind
from flexdisplay_bridge.store import DeviceStore
from PIL import Image


def test_legacy_commands_are_migrated_to_command_ids(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "devices": {
                    "X4-LEGACY": {
                        "device_id": "X4-LEGACY",
                        "pending_commands": [],
                        "dispatched_commands": ["install"],
                    },
                    "X3-PENDING": {
                        "device_id": "X3-PENDING",
                        "pending_commands": ["refresh", "install"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    store = DeviceStore(state_path)

    legacy = store.get("X4-LEGACY")
    assert legacy is not None
    assert legacy["dispatched_commands"] == []
    assert legacy["legacy_dispatched_commands"] == ["install"]
    assert legacy["legacy_commands_cleared_at"]

    pending = store.get("X3-PENDING")
    assert pending is not None
    assert pending["pending_commands"] == ["refresh"]
    assert pending["pending_command_id"] == "X3-PENDING-00000001"
    assert pending["legacy_pending_commands"] == ["refresh", "install"]
    assert pending["legacy_pending_install_cancelled_at"]

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["command_sequence"] == 1


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


def test_home_assistant_fetch_skips_local_device_pseudo_entities() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"state": "23.4", "attributes": {"unit_of_measurement": "°C"}}

    class Session:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url: str, **kwargs) -> Response:
            del kwargs
            self.urls.append(url)
            return Response()

    client = HomeAssistantClient(
        HomeAssistantConfig(base_url="http://homeassistant.test", token="test-token")
    )
    session = Session()
    client.session = session
    states, error = client.fetch(
        (
            EntityConfig("device.battery", "Device Battery"),
            EntityConfig("sensor.room_temperature", "Room Temperature"),
            EntityConfig("device.usb", "USB Power"),
        )
    )

    assert error == ""
    assert [state.entity_id for state in states] == ["sensor.room_temperature"]
    assert session.urls == ["http://homeassistant.test/api/states/sensor.room_temperature"]


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
                "live_mode": True,
                "manual_sleep_seconds": 1200,
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
        assert device["assigned_live_mode"] is True
        assert device["assigned_manual_sleep_seconds"] == 1200
        assert device["assigned_active_start"] == "07:00"
        assert device["assigned_active_end"] == "21:30"
        assert device["assigned_low_battery_percent"] == 40

        screen = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        assert screen.headers["x-flexdisplay-device-name"] == "Showroom Panel"
        assert screen.headers["x-flexdisplay-area"] == "Showroom"
        assert screen.headers["x-flexdisplay-profile"] == "showroom"
        assert screen.headers["x-flexdisplay-refresh-interval"] == "300"
        assert screen.headers["x-flexdisplay-live-mode"] == "true"
        assert screen.headers["x-flexdisplay-sleep-reason"] == "live_mode"


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
        command_id = screen.headers["x-flexdisplay-command-id"]
        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["pending_commands"] == []
        assert record["render_revision"] == 1
        assert record["dispatched_commands"] == ["refresh"]

        redelivered = client.get(
            "/api/v1/screen",
            headers={"X-FlexDisplay-ID": "X4-DEMO01"},
        )
        assert redelivered.headers["x-flexdisplay-commands"] == "refresh"
        assert redelivered.headers["x-flexdisplay-command-id"] == command_id

        stale = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-DEMO01",
                "X-FlexDisplay-Command-Result": "refresh:complete",
                "X-FlexDisplay-Command-ID": "X4-DEMO01-stale",
            },
        )
        assert stale.headers["x-flexdisplay-command-acknowledged"] == "false"
        assert client.get("/api/v1/devices/X4-DEMO01").json()["dispatched_commands"] == [
            "refresh"
        ]

        acknowledged = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-DEMO01",
                "X-FlexDisplay-Command-Result": "refresh:complete",
                "X-FlexDisplay-Command-ID": command_id,
            },
        )
        assert acknowledged.headers["x-flexdisplay-command-acknowledged"] == "true"
        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["last_command_result"] == "refresh:complete"
        assert record["last_command_id"] == command_id
        assert record["dispatched_commands"] == []
        assert record["command_history"][-1]["result"] == "refresh:complete"
        duplicate = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-DEMO01",
                "X-FlexDisplay-Command-Result": "refresh:complete",
                "X-FlexDisplay-Command-ID": command_id,
            },
        )
        assert duplicate.headers["x-flexdisplay-command-acknowledged"] == "true"
        assert len(client.get("/api/v1/devices/X4-DEMO01").json()["command_history"]) == 1


def test_remote_power_commands_and_queue_cancellation(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        devices={
            "X3-DEMO01": DeviceConfig(
                name="Remote X3",
                manual_sleep_seconds=1200,
            )
        },
    )
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X3-DEMO01"})

        client.post("/api/v1/devices/X3-DEMO01/commands/full-refresh")
        cancelled = client.delete("/api/v1/devices/X3-DEMO01/commands")
        assert cancelled.status_code == 200
        assert cancelled.json()["device"]["pending_commands"] == []

        client.post("/api/v1/devices/X3-DEMO01/commands/sleep")
        sleep = client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X3-DEMO01"})
        assert sleep.headers["x-flexdisplay-commands"] == "sleep"
        assert sleep.headers["x-flexdisplay-sleep-action"] == "scheduled"
        assert sleep.headers["x-flexdisplay-sleep-seconds"] == "1200"
        assert sleep.headers["x-flexdisplay-sleep-reason"] == "remote_command"

        client.post("/api/v1/devices/X3-DEMO01/commands/power-off")
        power_off = client.get(
            "/api/v1/screen",
            headers={"X-FlexDisplay-ID": "X3-DEMO01"},
        )
        assert power_off.headers["x-flexdisplay-commands"] == "power-off"
        assert power_off.headers["x-flexdisplay-sleep-action"] == "power_off"
        assert power_off.headers["x-flexdisplay-sleep-seconds"] == "0"


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
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
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


def test_firmware_rollout_requires_verified_usb_canary(tmp_path: Path) -> None:
    firmware = FirmwareConfig(
        version="1.4.1-flexdisplay.0.13.0",
        url="https://example.test/firmware.bin",
        sha256="cd" * 32,
        size=5_500_000,
        minimum_battery_percent=45,
        canary_required=True,
        require_usb_for_canary=True,
        max_parallel=1,
    )
    config = BridgeConfig(state_path=tmp_path / "state.json", firmware=firmware)
    with TestClient(create_app(config)) as client:
        for device_id in ("X3-CANARY", "X4-FLEET01", "X3-FLEET02"):
            client.get(
                "/api/v1/screen",
                headers={
                    "X-FlexDisplay-ID": device_id,
                    "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                    "X-FlexDisplay-SD-Ready": "true",
                    "X-FlexDisplay-USB-Connected": "true",
                    "X-FlexDisplay-Battery-Percent": "90",
                },
            )

        canary = client.post("/api/v1/devices/X3-CANARY/commands/install")
        assert canary.status_code == 200
        blocked = client.post("/api/v1/devices/X4-FLEET01/commands/install")
        assert blocked.status_code == 409
        assert "canary X3-CANARY" in blocked.json()["detail"]

        delivery = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-CANARY",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        command_id = delivery.headers["x-flexdisplay-command-id"]
        acknowledged = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-CANARY",
                "X-FlexDisplay-Firmware": firmware.version,
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
                "X-FlexDisplay-Command-ID": command_id,
                "X-FlexDisplay-Command-Result": "install:complete",
            },
        )
        assert acknowledged.headers["x-flexdisplay-command-acknowledged"] == "true"
        canary_record = client.get("/api/v1/devices/X3-CANARY").json()
        assert canary_record["firmware_canary_verified"] is True
        assert canary_record["firmware_update_status"] == "verified"

        fleet = client.post("/api/v1/devices/X4-FLEET01/commands/install")
        assert fleet.status_code == 200
        fleet_record = client.get("/api/v1/devices/X4-FLEET01").json()
        assert fleet_record["firmware_update_role"] == "fleet"
        assert fleet_record["firmware_rollout_status"] == "fleet_active"

        failed_delivery = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-FLEET01",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        failed_id = failed_delivery.headers["x-flexdisplay-command-id"]
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-FLEET01",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
                "X-FlexDisplay-Command-ID": failed_id,
                "X-FlexDisplay-Command-Result": "install:download-failed",
            },
        )
        paused = client.post("/api/v1/devices/X3-FLEET02/commands/install")
        assert paused.status_code == 409
        assert "Rollout paused after failure on X4-FLEET01" in paused.json()["detail"]


def test_usb_recovery_verification_is_guarded_and_audited(tmp_path: Path) -> None:
    firmware = FirmwareConfig(
        version="1.4.1-flexdisplay.0.13.0",
        url="https://example.test/firmware.bin",
        sha256="ab" * 32,
        size=5_500_000,
        canary_required=True,
        require_usb_for_canary=True,
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-secret",
        firmware=firmware,
    )
    authorized = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    with TestClient(create_app(config)) as client:
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-USB001",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        queued = client.post(
            "/api/v1/devices/X4-USB001/commands/install",
            headers=authorized,
        )
        assert queued.status_code == 200
        delivery = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-USB001",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        command_id = delivery.headers["x-flexdisplay-command-id"]

        not_recovered = client.post(
            "/api/v1/devices/X4-USB001/firmware/verify-usb-recovery",
            headers=authorized,
            json={
                "expected_target_version": firmware.version,
                "expected_command_id": command_id,
            },
        )
        assert not_recovered.status_code == 409
        assert "exact target firmware" in not_recovered.json()["detail"]

        recovered_checkin = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-USB001",
                "X-FlexDisplay-Firmware": firmware.version,
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        assert recovered_checkin.status_code == 200
        ready = client.get("/api/v1/devices/X4-USB001").json()
        assert ready["usb_recovery_verification_ready"] is True

        unauthenticated = client.post(
            "/api/v1/devices/X4-USB001/firmware/verify-usb-recovery",
            json={
                "expected_target_version": firmware.version,
                "expected_command_id": command_id,
            },
        )
        assert unauthenticated.status_code == 401

        wrong_command = client.post(
            "/api/v1/devices/X4-USB001/firmware/verify-usb-recovery",
            headers=authorized,
            json={
                "expected_target_version": firmware.version,
                "expected_command_id": "X4-USB001-wrong",
            },
        )
        assert wrong_command.status_code == 409
        assert "command ID" in wrong_command.json()["detail"]

        verified = client.post(
            "/api/v1/devices/X4-USB001/firmware/verify-usb-recovery",
            headers=authorized,
            json={
                "expected_target_version": firmware.version,
                "expected_command_id": command_id,
            },
        )
        assert verified.status_code == 200
        payload = verified.json()
        assert payload["verified"] is True
        assert payload["verification_method"] == "usb_recovery"
        assert payload["audit"]["reconciled_command_id"] == command_id
        assert payload["audit"]["observed_firmware"] == firmware.version

        record = client.get("/api/v1/devices/X4-USB001").json()
        assert record["firmware_update_status"] == "verified"
        assert record["firmware_verification_method"] == "usb_recovery"
        assert record["firmware_rollout_status"] == "canary_verified"
        assert record["firmware_canary_verified"] is True
        assert record["dispatched_commands"] == []
        assert record.get("dispatched_command_id") is None
        assert record["last_command_result"] == "install:usb-recovery-verified"
        assert record["usb_recovery_verification_ready"] is False
        assert record["usb_recovery_history"][-1]["reconciled_command_id"] == command_id


def test_usb_recovery_accepts_recent_matching_macos_evidence(tmp_path: Path) -> None:
    firmware = FirmwareConfig(
        version="1.4.1-flexdisplay.0.13.0",
        url="https://example.test/firmware.bin",
        sha256="ab" * 32,
        size=5_500_000,
    )
    config = BridgeConfig(state_path=tmp_path / "state.json", firmware=firmware)
    with TestClient(create_app(config)) as client:
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-9DD5C8",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        assert client.post("/api/v1/devices/X4-9DD5C8/commands/install").status_code == 200
        delivery = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-9DD5C8",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        command_id = delivery.headers["x-flexdisplay-command-id"]
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-9DD5C8",
                "X-FlexDisplay-Firmware": firmware.version,
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "false",
            },
        )
        record = client.get("/api/v1/devices/X4-9DD5C8").json()
        assert record["usb_recovery_verification_ready"] is False

        evidence = {
            "source": "macos_ioreg",
            "serial": "7C:E8:B1:9D:D5:C8",
            "port": "/dev/cu.usbmodem2101",
            "backup_sha256": "ca" * 32,
            "observed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        wrong_serial = client.post(
            "/api/v1/devices/X4-9DD5C8/firmware/verify-usb-recovery",
            json={
                "expected_target_version": firmware.version,
                "expected_command_id": command_id,
                "external_usb_evidence": {**evidence, "serial": "00:00:00:00:00:00"},
            },
        )
        assert wrong_serial.status_code == 409
        assert "USB power" in wrong_serial.json()["detail"]

        verified = client.post(
            "/api/v1/devices/X4-9DD5C8/firmware/verify-usb-recovery",
            json={
                "expected_target_version": firmware.version,
                "expected_command_id": command_id,
                "external_usb_evidence": evidence,
            },
        )
        assert verified.status_code == 200
        audit = verified.json()["audit"]
        assert audit["observed_usb_connected"] is False
        assert audit["external_usb_evidence"]["serial"] == "7CE8B19DD5C8"
        assert audit["external_usb_evidence"]["backup_sha256"] == "ca" * 32
        assert verified.json()["device"]["firmware_rollout_status"] == "canary_verified"


def test_usb_recovery_verifies_stuck_fleet_device_after_canary(tmp_path: Path) -> None:
    firmware = FirmwareConfig(
        version="1.4.1-flexdisplay.0.13.0",
        url="https://example.test/firmware.bin",
        sha256="ab" * 32,
        size=5_500_000,
        canary_required=True,
        require_usb_for_canary=True,
    )
    config = BridgeConfig(state_path=tmp_path / "state.json", firmware=firmware)
    with TestClient(create_app(config)) as client:
        for device_id in ("X4-CANARY", "X3-FLEET01"):
            client.get(
                "/api/v1/screen",
                headers={
                    "X-FlexDisplay-ID": device_id,
                    "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                    "X-FlexDisplay-SD-Ready": "true",
                    "X-FlexDisplay-USB-Connected": "true",
                },
            )

        assert client.post("/api/v1/devices/X4-CANARY/commands/install").status_code == 200
        canary_delivery = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-CANARY",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-CANARY",
                "X-FlexDisplay-Firmware": firmware.version,
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
                "X-FlexDisplay-Command-ID": canary_delivery.headers[
                    "x-flexdisplay-command-id"
                ],
                "X-FlexDisplay-Command-Result": "install:complete",
            },
        )

        assert client.post("/api/v1/devices/X3-FLEET01/commands/install").status_code == 200
        fleet_delivery = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-FLEET01",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        fleet_command_id = fleet_delivery.headers["x-flexdisplay-command-id"]
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-FLEET01",
                "X-FlexDisplay-Firmware": firmware.version,
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        ready = client.get("/api/v1/devices/X3-FLEET01").json()
        assert ready["usb_recovery_verification_ready"] is True

        verified = client.post(
            "/api/v1/devices/X3-FLEET01/firmware/verify-usb-recovery",
            json={
                "expected_target_version": firmware.version,
                "expected_command_id": fleet_command_id,
            },
        )
        assert verified.status_code == 200
        record = verified.json()["device"]
        assert record["firmware_update_status"] == "verified"
        assert record["firmware_rollout_status"] == "fleet_active"
        assert record["firmware_canary_device_id"] == "X4-CANARY"
        assert record["dispatched_commands"] == []
        assert record["last_usb_recovery_verification"]["role"] == "fleet"


def test_firmware_preflight_rejects_missing_sd_and_invalid_manifest(tmp_path: Path) -> None:
    invalid = FirmwareConfig(
        version="1.4.1-flexdisplay.0.13.0",
        url="https://example.test/firmware.bin",
        sha256="not-a-sha",
        size=5_500_000,
    )
    with TestClient(create_app(BridgeConfig(state_path=tmp_path / "bad.json", firmware=invalid))) as client:
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-BAD001",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        rejected = client.post("/api/v1/devices/X3-BAD001/commands/install")
        assert rejected.status_code == 409
        assert "SHA-256" in rejected.json()["detail"]

    valid = FirmwareConfig(
        version="1.4.1-flexdisplay.0.13.0",
        url="https://example.test/firmware.bin",
        sha256="ef" * 32,
        size=5_500_000,
    )
    with TestClient(create_app(BridgeConfig(state_path=tmp_path / "sd.json", firmware=valid))) as client:
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-NOSD01",
                "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.12.0",
                "X-FlexDisplay-USB-Connected": "true",
            },
        )
        rejected = client.post("/api/v1/devices/X3-NOSD01/commands/install")
        assert rejected.status_code == 409
        assert "SD card" in rejected.json()["detail"]


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
