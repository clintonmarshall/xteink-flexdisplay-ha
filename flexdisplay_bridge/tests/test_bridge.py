from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
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
    PageActivationConfig,
)
from flexdisplay_bridge.dashboards import (
    DashboardPage,
    build_dashboard_pages,
    select_active_pages,
)
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


def test_image_tiles_fetch_ha_entity_picture_with_auth_but_external_urls_without_it() -> None:
    image_output = io.BytesIO()
    Image.new("RGB", (48, 32), (80, 150, 210)).save(image_output, format="PNG")
    image_content = image_output.getvalue()

    class Response:
        def __init__(self, *, payload: dict | None = None, content: bytes = b"", url: str):
            self.payload = payload
            self.content = content
            self.url = url
            self.headers = {
                "Content-Type": "image/png",
                "Content-Length": str(len(content)),
            } if content else {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            assert self.payload is not None
            return self.payload

        def iter_content(self, chunk_size: int):
            for offset in range(0, len(self.content), chunk_size):
                yield self.content[offset : offset + chunk_size]

    class Session:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def get(self, url: str, **kwargs) -> Response:
            self.calls.append((url, kwargs))
            if url.endswith("/api/states/camera.front_door"):
                return Response(
                    url=url,
                    payload={
                        "state": "streaming",
                        "attributes": {"entity_picture": "/api/camera_proxy/camera.front_door"},
                    },
                )
            return Response(url=url, content=image_content)

    client = HomeAssistantClient(
        HomeAssistantConfig(base_url="http://homeassistant.test:8123", token="secret")
    )
    session = Session()
    client.session = session
    states, error = client.fetch(
        (
            EntityConfig(
                "camera.front_door",
                "Front Door",
                style="image",
                image_fit="contain",
            ),
            EntityConfig(
                "image_url.page_1_tile_2",
                "Weather Map",
                style="image",
                image_url="https://images.example.test/map.png",
            ),
        )
    )

    assert error == ""
    assert len(states) == 2
    assert all(state.available and state.image_bytes == image_content for state in states)
    assert states[0].image_fit == "contain"
    ha_picture_call = next(call for call in session.calls if "/api/camera_proxy/" in call[0])
    external_call = next(call for call in session.calls if "images.example.test" in call[0])
    assert ha_picture_call[1]["headers"]["Authorization"] == "Bearer secret"
    assert "Authorization" not in external_call[1]["headers"]


def test_external_image_tile_works_without_a_home_assistant_token() -> None:
    image_output = io.BytesIO()
    Image.new("L", (24, 24), 128).save(image_output, format="PNG")
    image_content = image_output.getvalue()

    class Response:
        url = "http://display.local/photo.png"
        headers = {"Content-Type": "image/png"}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            del chunk_size
            yield image_content

    class Session:
        def get(self, url: str, **kwargs) -> Response:
            assert url == Response.url
            assert "Authorization" not in kwargs["headers"]
            return Response()

    client = HomeAssistantClient(HomeAssistantConfig(token=""))
    client.session = Session()
    states, error = client.fetch(
        (
            EntityConfig(
                "image_url.page_1_tile_1",
                "Local Photo",
                style="image",
                image_url=Response.url,
            ),
        )
    )

    assert error == ""
    assert states[0].available is True
    assert states[0].image_bytes == image_content


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


def test_new_physical_button_events_navigate_once(tmp_path: Path) -> None:
    profile = DashboardProfileConfig(
        name="wall",
        pages=(
            DashboardPageConfig("CLIMATE", ()),
            DashboardPageConfig("ENERGY", ()),
        ),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
        profiles={"wall": profile},
        devices={"X3-DEMO01": DeviceConfig(name="Test X3", profile="wall")},
    )
    with TestClient(create_app(config)) as client:
        right = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-DEMO01",
                "X-FlexDisplay-Button-Events": "1,right,pressed,120000",
            },
        )
        assert right.headers["x-flexdisplay-page"] == "2"
        assert right.headers["x-flexdisplay-page-title"] == "ENERGY"

        duplicate = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-DEMO01",
                "X-FlexDisplay-Button-Events": "1,right,pressed,120000",
            },
        )
        assert duplicate.headers["x-flexdisplay-page"] == "2"

        left = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-DEMO01",
                "X-FlexDisplay-Button-Events": "2,left,pressed,121000",
            },
        )
        assert left.headers["x-flexdisplay-page"] == "1"
        assert left.headers["x-flexdisplay-page-title"] == "CLIMATE"
        record = client.get("/api/v1/devices/X3-DEMO01").json()
        assert record["button_press_count"] == 2


def test_configurable_double_press_calls_home_assistant_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def fake_call_service(
        _client: HomeAssistantClient,
        service: str,
        entity_id: str = "",
        data: dict | None = None,
    ) -> tuple[bool, str]:
        calls.append((service, entity_id, data))
        return True, f"called {service}"

    monkeypatch.setattr(HomeAssistantClient, "call_service", fake_call_service)
    profile = DashboardProfileConfig(
        name="wall",
        pages=(DashboardPageConfig("CLIMATE", ()), DashboardPageConfig("ENERGY", ())),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token="test-token"),
        profiles={"wall": profile},
        devices={"X3-DEMO01": DeviceConfig(name="Test X3", profile="wall")},
    )
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X3-DEMO01"})
        saved = client.put(
            "/api/v1/devices/X3-DEMO01/button-actions",
            json={
                "mappings": [
                    {
                        "mode": "home_assistant",
                        "button": "right",
                        "gesture": "double",
                        "action": {
                            "type": "home_assistant",
                            "service": "light.toggle",
                            "entity_id": "light.showroom",
                            "data": {"transition": 1},
                        },
                    }
                ]
            },
        )
        assert saved.status_code == 200

        response = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-DEMO01",
                "X-FlexDisplay-Mode": "home_assistant",
                "X-FlexDisplay-Button-Events": (
                    "7,right,pressed,121000,double,home_assistant"
                ),
            },
        )
        assert response.headers["x-flexdisplay-page-title"] == "CLIMATE"
        assert calls == [("light.toggle", "light.showroom", {"transition": 1})]

        # Firmware retries remain replay-safe.
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-DEMO01",
                "X-FlexDisplay-Mode": "home_assistant",
                "X-FlexDisplay-Button-Events": (
                    "7,right,pressed,121000,double,home_assistant"
                ),
            },
        )
        assert len(calls) == 1
        record = client.get("/api/v1/devices/X3-DEMO01").json()
        assert record["last_button_gesture"] == "double"
        assert record["last_button_action_result"] == "called light.toggle"
        event = record["recent_button_events"][-1]
        assert event["configured_action"]["service"] == "light.toggle"
        assert event["configured_action"]["success"] is True


def test_reader_mode_events_never_execute_home_assistant_mapping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        HomeAssistantClient,
        "call_service",
        lambda _client, service, entity_id="", data=None: (
            calls.append(service) is None,
            f"called {service}",
        ),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token="test-token"),
    )
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        client.put(
            "/api/v1/devices/X4-DEMO01/button-actions",
            json={
                "mappings": [
                    {
                        "button": "confirm",
                        "gesture": "long",
                        "action": {
                            "type": "home_assistant",
                            "service": "scene.turn_on",
                            "entity_id": "scene.showroom",
                        },
                    }
                ]
            },
        )
        response = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-DEMO01",
                "X-FlexDisplay-Mode": "home_assistant",
                "X-FlexDisplay-Button-Events": "9,confirm,pressed,555,long,reader",
            },
        )
        assert response.status_code == 200
        assert calls == []
        record = client.get("/api/v1/devices/X4-DEMO01").json()
        assert record["recent_button_events"][-1]["mode"] == "reader"
        assert record["last_button_action_result"] == "no action"


def test_button_action_validation_reserves_recovery_and_admin_services(tmp_path: Path) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json")
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers={"X-FlexDisplay-ID": "X4-DEMO01"})
        reserved = client.put(
            "/api/v1/devices/X4-DEMO01/button-actions",
            json={
                "mappings": [
                    {
                        "button": "power",
                        "gesture": "long",
                        "action": {"type": "none"},
                    }
                ]
            },
        )
        assert reserved.status_code == 400

        administrative = client.put(
            "/api/v1/devices/X4-DEMO01/button-actions",
            json={
                "mappings": [
                    {
                        "button": "confirm",
                        "gesture": "long",
                        "action": {
                            "type": "home_assistant",
                            "service": "homeassistant.restart",
                        },
                    }
                ]
            },
        )
        assert administrative.status_code == 400


def test_dashboard_studio_persists_profiles_and_renders_x3_preview(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    config = BridgeConfig(
        state_path=state_path,
        api_key="studio-secret",
        home_assistant=HomeAssistantConfig(token=""),
    )
    profile = {
        "auto_rotate_seconds": 300,
        "pages": [
            {
                "title": "Showroom",
                "layout": "rows",
                "activation": {
                    "type": "schedule",
                    "start": "07:00",
                    "end": "21:00",
                },
                "entities": [
                    {
                        "entity_id": "device.battery",
                        "label": "Display Battery",
                        "unit": "%",
                        "icon": "battery",
                        "style": "gauge",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    {
                        "entity_id": "device.wifi",
                        "label": "Wi-Fi Signal",
                        "unit": "dBm",
                        "icon": "wifi",
                        "style": "progress",
                        "minimum": -100,
                        "maximum": -30,
                    },
                ],
            }
        ],
    }
    headers = {"X-FlexDisplay-Bridge-Key": "studio-secret"}
    with TestClient(create_app(config)) as client:
        assert client.get("/studio/").status_code == 200
        doubled_ingress = client.get("http://testserver//studio/")
        assert doubled_ingress.status_code == 200
        assert "FlexDisplay Dashboard Studio" in doubled_ingress.text
        assert client.get("/api/v1/studio").status_code == 401
        saved = client.put(
            "/api/v1/studio/profiles/showroom",
            headers=headers,
            json=profile,
        )
        assert saved.status_code == 200
        assert saved.json()["profile"]["pages"][0]["layout"] == "rows"
        assert saved.json()["profile"]["pages"][0]["activation"]["type"] == "schedule"
        preview = client.post(
            "/api/v1/studio/preview",
            headers=headers,
            json={"model": "X3", "profile": profile, "page_index": 0},
        )
        assert preview.status_code == 200
        with Image.open(io.BytesIO(preview.content)) as image:
            assert image.size == (528, 792)
            assert image.mode == "1"

    persisted = json.loads(
        (tmp_path / "flexdisplay-dashboards.json").read_text(encoding="utf-8")
    )
    assert persisted["profiles"]["showroom"]["auto_rotate_seconds"] == 300
    assert persisted["version"] == 2
    assert persisted["profiles"]["showroom"]["pages"][0]["activation"]["start"] == "07:00"

    with TestClient(create_app(config)) as client:
        studio = client.get("/api/v1/studio", headers=headers).json()
        assert [item["name"] for item in studio["profiles"]] == ["default", "showroom"]


def test_dashboard_studio_uses_searchable_full_catalogue_entity_picker(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
    )

    with TestClient(create_app(config)) as client:
        response = client.get("/studio/")

    assert response.status_code == 200
    assert "function searchEntities" in response.text
    assert "function bindEntityPicker" in response.text
    assert "entity-picker-results" in response.text
    assert "keep typing to narrow" in response.text
    assert 'list="entityOptions"' not in response.text


def test_dashboard_studio_assignment_drives_device_screen(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
    )
    profile = {
        "pages": [
            {
                "title": "Device Status",
                "layout": "single",
                "entities": [
                    {
                        "entity_id": "device.battery",
                        "label": "Battery",
                        "unit": "%",
                        "icon": "battery",
                        "style": "value",
                        "minimum": 0,
                        "maximum": 100,
                    }
                ],
            }
        ]
    }
    with TestClient(create_app(config)) as client:
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-STUDIO1",
                "X-FlexDisplay-Battery-Percent": "84",
            },
        )
        assert client.put(
            "/api/v1/studio/profiles/wall",
            json=profile,
        ).status_code == 200
        assigned = client.put(
            "/api/v1/devices/X4-STUDIO1/provision",
            json={"profile": "wall"},
        )
        assert assigned.status_code == 200
        screen = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-STUDIO1",
                "X-FlexDisplay-Battery-Percent": "84",
            },
        )
        assert screen.headers["x-flexdisplay-profile"] == "wall"
        assert screen.headers["x-flexdisplay-page-title"] == "DEVICE STATUS"
        assert screen.headers["x-flexdisplay-page-count"] == "1"


def test_dashboard_studio_rejects_unsafe_profiles(tmp_path: Path) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json")
    with TestClient(create_app(config)) as client:
        invalid_name = client.put(
            "/api/v1/studio/profiles/not%20safe",
            json={"pages": [{"title": "Overview", "entities": []}]},
        )
        assert invalid_name.status_code == 400
        too_many_tiles = client.put(
            "/api/v1/studio/profiles/wall",
            json={
                "pages": [
                    {
                        "title": "Overview",
                        "entities": [
                            {"entity_id": f"sensor.value_{index}"}
                            for index in range(5)
                        ],
                    }
                ]
            },
        )
        assert too_many_tiles.status_code == 400
        invalid_condition = client.put(
            "/api/v1/studio/profiles/wall",
            json={
                "pages": [
                    {
                        "title": "Alert",
                        "activation": {
                            "type": "condition",
                            "entity_id": "not-an-entity",
                            "operator": "equals",
                            "value": "on",
                        },
                        "entities": [],
                    }
                ]
            },
        )
        assert invalid_condition.status_code == 400
        embedded_credentials = client.put(
            "/api/v1/studio/profiles/wall",
            json={
                "pages": [
                    {
                        "title": "Camera",
                        "entities": [
                            {
                                "entity_id": "",
                                "style": "image",
                                "image_url": "https://user:secret@example.test/camera.jpg",
                            }
                        ],
                    }
                ]
            },
        )
        assert embedded_credentials.status_code == 400


def test_dashboard_studio_accepts_an_external_image_without_an_ha_entity(tmp_path: Path) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json")
    profile = {
        "pages": [
            {
                "title": "Photo",
                "layout": "single",
                "entities": [
                    {
                        "entity_id": "",
                        "label": "Showroom",
                        "style": "image",
                        "image_url": "http://media.local/showroom.jpg",
                        "image_fit": "contain",
                    }
                ],
            }
        ]
    }
    with TestClient(create_app(config)) as client:
        saved = client.put("/api/v1/studio/profiles/photos", json=profile)

    assert saved.status_code == 200
    tile = saved.json()["profile"]["pages"][0]["entities"][0]
    assert tile["entity_id"] == "image_url.page_1_tile_1"
    assert tile["image_url"] == "http://media.local/showroom.jpg"
    assert tile["image_fit"] == "contain"


def test_state_aware_alert_priority_expiry_and_scheduled_sets() -> None:
    now = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)  # 10:00 Australia/Melbourne
    pages = (
        DashboardPage("DEFAULT", (), activation=PageActivationConfig()),
        DashboardPage(
            "DAYTIME",
            (),
            activation=PageActivationConfig(type="schedule", start="06:00", end="18:00"),
        ),
        DashboardPage(
            "DOOR OPEN",
            (),
            activation=PageActivationConfig(
                type="condition",
                entity_id="binary_sensor.front_door",
                operator="on",
                priority=80,
                expires_after_seconds=300,
            ),
        ),
        DashboardPage(
            "FIRE ALARM",
            (),
            activation=PageActivationConfig(
                type="condition",
                entity_id="binary_sensor.fire_alarm",
                operator="on",
                priority=100,
            ),
        ),
    )
    states = [
        EntityState(
            "binary_sensor.front_door",
            "Front Door",
            "on",
            "",
            True,
            last_changed=now - timedelta(seconds=60),
        ),
        EntityState(
            "binary_sensor.fire_alarm",
            "Fire Alarm",
            "on",
            "",
            True,
            last_changed=now - timedelta(seconds=30),
        ),
    ]

    active, reason = select_active_pages(
        pages,
        states,
        {},
        "Australia/Melbourne",
        now,
    )
    assert reason == "alert"
    assert [page.title for page in active] == ["FIRE ALARM", "DOOR OPEN", "DAYTIME"]

    expired_states = [
        EntityState(
            "binary_sensor.front_door",
            "Front Door",
            "on",
            "",
            True,
            last_changed=now - timedelta(seconds=301),
        ),
        EntityState(
            "binary_sensor.fire_alarm",
            "Fire Alarm",
            "off",
            "",
            True,
            last_changed=now,
        ),
    ]
    scheduled, reason = select_active_pages(
        pages,
        expired_states,
        {},
        "Australia/Melbourne",
        now,
    )
    assert reason == "schedule"
    assert [page.title for page in scheduled] == ["DAYTIME"]


def test_state_aware_device_alert_restores_default_playlist(tmp_path: Path) -> None:
    profile = DashboardProfileConfig(
        name="aware",
        pages=(
            DashboardPageConfig("OVERVIEW"),
            DashboardPageConfig(
                "LOW BATTERY",
                (
                    EntityConfig(
                        "device.battery",
                        "Display Battery",
                        "%",
                        "battery",
                        "gauge",
                    ),
                ),
                "single",
                PageActivationConfig(
                    type="condition",
                    entity_id="device.battery",
                    operator="below",
                    value="20",
                    priority=90,
                ),
            ),
        ),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        profiles={"aware": profile},
        default_profile="aware",
    )
    with TestClient(create_app(config)) as client:
        alert = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-AWARE1",
                "X-FlexDisplay-Battery-Percent": "12",
            },
        )
        assert alert.headers["x-flexdisplay-page-selection"] == "alert"
        assert alert.headers["x-flexdisplay-page-title"] == "LOW BATTERY"
        assert alert.headers["x-flexdisplay-page-count"] == "2"

        restored = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-AWARE1",
                "X-FlexDisplay-Battery-Percent": "82",
            },
        )
        assert restored.headers["x-flexdisplay-page-selection"] == "default"
        assert restored.headers["x-flexdisplay-page-title"] == "OVERVIEW"
        assert restored.headers["x-flexdisplay-page-count"] == "1"


def test_dashboard_renderer_supports_visual_tile_styles() -> None:
    renderer = DashboardRenderer()
    for style in ("value", "gauge", "progress", "history", "qr"):
        entity = EntityState(
            "sensor.visual",
            style.title(),
            "https://example.test" if style == "qr" else "64",
            "" if style == "qr" else "%",
            True,
            "battery",
            style,
            0,
            100,
            (20, 30, 25, 50, 64) if style == "history" else (),
        )
        image = renderer.render(
            title="VISUAL",
            device={"device_id": "X4-PREVIEW", "battery_percent": 80, "rssi": -50},
            width=480,
            height=800,
            entities=(entity,),
            layout="single",
        )
        with Image.open(io.BytesIO(image)) as rendered:
            assert rendered.size == (480, 800)
            assert rendered.mode == "1"


def test_dashboard_renderer_crops_and_dithers_image_tiles() -> None:
    source = io.BytesIO()
    gradient = Image.new("L", (180, 80))
    for x in range(gradient.width):
        for y in range(gradient.height):
            gradient.putpixel((x, y), round(255 * x / gradient.width))
    gradient.save(source, format="PNG")
    renderer = DashboardRenderer()

    for fit in ("cover", "contain"):
        image = renderer.render(
            title="CAMERA",
            device={"device_id": "X3-PREVIEW", "battery_percent": 80, "rssi": -50},
            width=528,
            height=792,
            entities=(
                EntityState(
                    "camera.showroom",
                    "Showroom Camera",
                    "Image",
                    "",
                    True,
                    style="image",
                    image_bytes=source.getvalue(),
                    image_fit=fit,
                ),
            ),
            layout="single",
        )
        with Image.open(io.BytesIO(image)) as rendered:
            assert rendered.size == (528, 792)
            assert rendered.mode == "1"
            assert rendered.getextrema() == (0, 255)


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
