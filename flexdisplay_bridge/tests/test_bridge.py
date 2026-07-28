from __future__ import annotations

import io
import hashlib
import json
import zipfile
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
    MqttConfig,
    PageActivationConfig,
)
from flexdisplay_bridge.dashboards import (
    DashboardPage,
    build_dashboard_pages,
    select_active_pages,
)
from flexdisplay_bridge.home_assistant import EntityState, HomeAssistantClient
from flexdisplay_bridge.mqtt_service import MqttService
from flexdisplay_bridge.photo_frame import PhotoFrameMediaStore
from flexdisplay_bridge.renderer import DashboardRenderer, _icon_kind
from flexdisplay_bridge.store import DeviceStore
from PIL import Image


def _content_pack_zip(version: str = "ldcs-1") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("photos/welcome.png", b"fleet-content")
        archive.writestr(
            "content-pack.json",
            json.dumps(
                {
                    "version": version,
                    "name": "LDCS welcome pack",
                    "files": [
                        {
                            "source": "photos/welcome.png",
                            "target": "/photos/flexdisplay/welcome.png",
                        }
                    ],
                }
            ),
        )
    return output.getvalue()


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


def test_v022_content_pack_rollout_is_acknowledged_per_device(tmp_path: Path) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json")
    with TestClient(create_app(config)) as client:
        first = client.get(
            "/api/v1/screen",
            headers={"X-FlexDisplay-ID": "X3-CONTENT"},
        )
        assert first.status_code == 200

        uploaded = client.post(
            "/api/v1/content-packs",
            content=_content_pack_zip(),
            headers={"Content-Type": "application/zip"},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["pack"]["version"] == "ldcs-1"

        rollout = client.post(
            "/api/v1/content-packs/ldcs-1/rollout",
            json={"device_ids": ["X3-CONTENT"]},
        )
        assert rollout.status_code == 200

        assigned = client.get(
            "/api/v1/screen",
            headers={"X-FlexDisplay-ID": "X3-CONTENT"},
        )
        assert assigned.status_code == 200
        assert assigned.headers["x-flexdisplay-content-version"] == "ldcs-1"
        manifest_url = assigned.headers["x-flexdisplay-content-manifest-url"]
        manifest = client.get(manifest_url)
        assert manifest.status_code == 200
        assert hashlib.sha256(manifest.content).hexdigest() == assigned.headers[
            "x-flexdisplay-content-manifest-sha256"
        ]

        acknowledged = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-CONTENT",
                "X-FlexDisplay-Content-Version": "ldcs-1",
                "X-FlexDisplay-Content-Status": "installed",
            },
        )
        assert acknowledged.status_code == 200
        assert "x-flexdisplay-content-version" not in acknowledged.headers
        state = client.get("/api/v1/content-packs").json()
        assert state["assignments"]["X3-CONTENT"]["status"] == "installed"


def test_v021_screen_advertises_cached_branded_fetch_asset(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
        devices={
            "X4-BRAND01": DeviceConfig(
                name="Showroom Display",
                area="Melbourne",
                model="X4",
                width=480,
                height=800,
            )
        },
    )
    with TestClient(create_app(config)) as client:
        saved = client.put(
            "/api/v1/loading-screens/X4-BRAND01",
            json={
                "enabled": True,
                "policy": "manual",
                "layout": "identity",
                "headline": "Welcome to {area}",
                "message": "Updating {device_name}",
                "owner_name": "LDCS",
                "show_device_name": True,
                "show_owner": True,
                "show_area": True,
            },
        )
        assert saved.status_code == 200

        screen = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-BRAND01",
                "X-FlexDisplay-Width": "480",
                "X-FlexDisplay-Height": "800",
            },
        )
        assert screen.status_code == 200
        assert screen.headers["x-flexdisplay-loading-enabled"] == "true"
        assert screen.headers["x-flexdisplay-loading-policy"] == "manual"
        assert len(screen.headers["x-flexdisplay-loading-sha256"]) == 64
        assert screen.headers["x-flexdisplay-loading-url"].endswith(
            "/api/v1/devices/X4-BRAND01/loading-screen.bmp"
        )

        asset = client.get("/api/v1/devices/X4-BRAND01/loading-screen.bmp")
        assert asset.status_code == 200
        assert asset.headers["content-type"] == "image/bmp"
        assert (
            hashlib.sha256(asset.content).hexdigest()
            == screen.headers["x-flexdisplay-loading-sha256"]
        )
        with Image.open(io.BytesIO(asset.content)) as image:
            assert image.size == (480, 800)
            assert image.mode == "1"


def test_v021_loading_screen_studio_upload_preview_and_inheritance(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
        devices={"X3-BRAND01": DeviceConfig(name="Kitchen", model="X3")},
    )
    logo = Image.new("RGB", (220, 80), "white")
    logo_output = io.BytesIO()
    logo.save(logo_output, format="PNG")

    with TestClient(create_app(config)) as client:
        client.get(
            "/api/v1/screen",
            headers={"X-FlexDisplay-ID": "X3-BRAND01"},
        )
        uploaded = client.post(
            "/api/v1/loading-screens/default/logo",
            content=logo_output.getvalue(),
            headers={"X-FlexDisplay-Filename": "company-logo.png"},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["config"]["logo_filename"] == "company-logo.png"
        assert uploaded.json()["refresh_queued"] == ["X3-BRAND01"]

        inherited = client.get("/api/v1/loading-screens/X3-BRAND01").json()
        assert inherited["config"]["inherited"] is True
        assert inherited["config"]["logo_sha256"]

        preview = client.post(
            "/api/v1/loading-screens/default/preview",
            json={
                "model": "X3",
                "device_id": "X3-BRAND01",
                "config": {
                    **inherited["config"],
                    "headline": "Hello {device_name}",
                    "message": "Owned by {owner}",
                    "owner_name": "Facilities",
                    "show_owner": True,
                },
            },
        )
        assert preview.status_code == 200
        with Image.open(io.BytesIO(preview.content)) as image:
            assert image.size == (528, 792)

        overridden = client.put(
            "/api/v1/loading-screens/X3-BRAND01",
            json={"headline": "Device override", "message": "Please wait"},
        )
        assert overridden.status_code == 200
        assert overridden.json()["config"]["inherited"] is False
        reset = client.delete("/api/v1/loading-screens/X3-BRAND01")
        assert reset.status_code == 200
        assert reset.json()["config"]["inherited"] is True


def test_v020_screen_history_can_preview_and_resend_exact_image(tmp_path: Path) -> None:
    profile = DashboardProfileConfig(
        name="two-pages",
        pages=(
            DashboardPageConfig(title="FIRST"),
            DashboardPageConfig(title="SECOND"),
        ),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
        profiles={"two-pages": profile},
        default_profile="two-pages",
        devices={
            "X4-HISTORY": DeviceConfig(
                name="History X4",
                model="X4",
                profile="two-pages",
            )
        },
    )
    headers = {
        "X-FlexDisplay-ID": "X4-HISTORY",
        "X-FlexDisplay-Width": "480",
        "X-FlexDisplay-Height": "800",
        "X-FlexDisplay-SD-Ready": "true",
    }
    with TestClient(create_app(config)) as client:
        first = client.get("/api/v1/screen", headers=headers)
        assert first.status_code == 200
        client.post("/api/v1/devices/X4-HISTORY/commands/next")
        second = client.get("/api/v1/screen", headers=headers)
        assert second.status_code == 200
        assert first.content != second.content

        listing = client.get("/api/v1/devices/X4-HISTORY/screens").json()
        assert len(listing["screens"]) == 2
        assert listing["screens"][0]["title"] == "SECOND"
        assert listing["screens"][1]["title"] == "FIRST"

        current = client.get("/api/v1/devices/X4-HISTORY/screens/current")
        assert current.status_code == 200
        assert current.content == second.content
        current_png = client.get(
            "/api/v1/devices/X4-HISTORY/screens/current.png"
        )
        assert current_png.status_code == 200
        assert current_png.headers["content-type"] == "image/png"
        assert current_png.content == second.content

        older = listing["screens"][1]
        queued = client.post(
            f"/api/v1/devices/X4-HISTORY/screens/{older['id']}/resend"
        )
        assert queued.status_code == 200
        restored = client.get("/api/v1/screen", headers=headers)
        assert restored.status_code == 200
        assert restored.content == first.content
        assert restored.headers["x-flexdisplay-screen-restored"] == older["id"]


def test_v020_mqtt_discovery_has_full_app_only_entities_and_hacs_cleanup() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.messages: list[tuple[str, object, bool]] = []

        def publish(self, topic: str, payload: object, retain: bool = False):
            self.messages.append((topic, payload, retain))

    state = {
        "device_id": "X4-MQTT01",
        "model": "X4",
        "firmware": "1.4.1-flexdisplay.0.19.0",
        "latest_firmware": "1.4.1-flexdisplay.0.20.0",
        "available_profiles": ["default", "energy"],
        "available_modes": ["home_assistant", "photo_frame"],
    }
    service = MqttService(
        MqttConfig(enabled=True, entity_source="mqtt"),
        lambda device_id, command, payload: None,
    )
    client = FakeClient()
    service.client = client
    service.connected = True
    service.publish_device(
        "X4-MQTT01",
        DeviceConfig(name="MQTT X4", model="X4"),
        state,
    )
    service.publish_screen("X4-MQTT01", b"png-screen")

    topics = {message[0]: message[1] for message in client.messages}
    assert "homeassistant/update/x4_mqtt01/firmware/config" in topics
    assert "homeassistant/event/x4_mqtt01/physical_button/config" in topics
    assert "homeassistant/select/x4_mqtt01/profile/config" in topics
    assert "homeassistant/number/x4_mqtt01/refresh_interval/config" in topics
    assert "homeassistant/switch/x4_mqtt01/intelligent_sleep/config" in topics
    assert "homeassistant/text/x4_mqtt01/timezone/config" in topics
    assert "homeassistant/number/x4_mqtt01/manual_wake_grace/config" in topics
    assert "homeassistant/image/x4_mqtt01/current_screen/config" in topics
    assert topics["flexdisplay/X4-MQTT01/screen"] == b"png-screen"
    assert json.loads(
        topics["homeassistant/select/x4_mqtt01/profile/config"]
    )["options"] == ["default", "energy"]
    assert json.loads(
        topics["homeassistant/update/x4_mqtt01/firmware/config"]
    )["payload_install"] == "PRESS"

    cleanup = MqttService(
        MqttConfig(enabled=True, entity_source="hacs"),
        lambda device_id, command, payload: None,
    )
    cleanup_client = FakeClient()
    cleanup.client = cleanup_client
    cleanup.connected = True
    cleanup.publish_device(
        "X4-MQTT01",
        DeviceConfig(name="MQTT X4", model="X4"),
        state,
    )
    discovery_payloads = [
        payload
        for topic, payload, retain in cleanup_client.messages
        if topic.startswith("homeassistant/") and topic.endswith("/config") and retain
    ]
    assert discovery_payloads
    assert set(discovery_payloads) == {""}


def test_v020_mqtt_controls_update_provisioning_without_hacs(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
        mqtt=MqttConfig(enabled=False, entity_source="mqtt"),
        profiles={
            "default": DashboardProfileConfig(
                name="default",
                pages=(DashboardPageConfig(title="OVERVIEW"),),
            )
        },
    )
    headers = {
        "X-FlexDisplay-ID": "X3-APPMQTT",
        "X-FlexDisplay-Width": "528",
        "X-FlexDisplay-Height": "792",
    }
    app = create_app(config)
    with TestClient(app) as client:
        client.get("/api/v1/screen", headers=headers)
        app.state.mqtt.on_command("X3-APPMQTT", "set-refresh-interval", "1800")
        app.state.mqtt.on_command("X3-APPMQTT", "set-mode", "photo_frame")
        app.state.mqtt.on_command("X3-APPMQTT", "set-active-start", "07:30")

        device = client.get("/api/v1/devices/X3-APPMQTT").json()
        assert device["assigned_refresh_interval_seconds"] == 1800
        assert device["assigned_mode"] == "photo_frame"
        assert device["assigned_active_start"] == "07:30"
        assert device["last_management_action"] == "set-active-start"
        assert device["last_management_action_success"] is True


def test_v020_stale_install_is_released_and_audited(tmp_path: Path) -> None:
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "devices": {
                    "X3-STALE1": {
                        "device_id": "X3-STALE1",
                        "pending_commands": [],
                        "dispatched_commands": ["install"],
                        "dispatched_command_id": "X3-STALE1-00000001",
                        "firmware_update_target": "1.0.0",
                        "firmware_update_stage_at": old,
                    }
                },
                "firmware_rollout": {
                    "target_version": "1.0.0",
                    "status": "canary_active",
                    "canary_device_id": "X3-STALE1",
                },
            }
        ),
        encoding="utf-8",
    )
    store = DeviceStore(state_path)

    assert store.expire_stale_firmware_installs(1800) == ["X3-STALE1"]
    record = store.get("X3-STALE1")
    assert record is not None
    assert record["dispatched_commands"] == []
    assert record["firmware_update_status"] == "failed"
    assert record["firmware_update_error"] == "install:stale-timeout"
    assert store.firmware_rollout()["status"] == "failed"


def test_v020_queued_install_can_wait_for_a_sleeping_device(tmp_path: Path) -> None:
    old = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "devices": {
                    "X3-SLEEP1": {
                        "device_id": "X3-SLEEP1",
                        "pending_commands": ["install"],
                        "pending_command_id": "X3-SLEEP1-00000001",
                        "dispatched_commands": [],
                        "firmware_update_stage_at": old,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    store = DeviceStore(state_path)

    assert store.expire_stale_firmware_installs(1800) == []
    assert store.get("X3-SLEEP1")["pending_commands"] == ["install"]


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


def test_camera_picture_keeps_supervisor_core_proxy_prefix() -> None:
    image_output = io.BytesIO()
    Image.new("RGB", (48, 32), (80, 150, 210)).save(image_output, format="JPEG")
    image_content = image_output.getvalue()

    class Response:
        def __init__(self, *, payload: dict | None = None, content: bytes = b"", url: str):
            self.payload = payload
            self.content = content
            self.url = url
            self.headers = (
                {
                    "Content-Type": "image/jpeg",
                    "Content-Length": str(len(content)),
                }
                if content
                else {}
            )

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            assert self.payload is not None
            return self.payload

        def iter_content(self, chunk_size: int):
            del chunk_size
            yield self.content

    class Session:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def get(self, url: str, **kwargs) -> Response:
            self.calls.append((url, kwargs))
            if url.endswith("/core/api/states/camera.alfresco"):
                return Response(
                    url=url,
                    payload={
                        "state": "streaming",
                        "attributes": {
                            "entity_picture": (
                                "/api/camera_proxy/camera.alfresco"
                                "?token=signed-camera-token"
                            )
                        },
                    },
                )
            return Response(url=url, content=image_content)

    client = HomeAssistantClient(
        HomeAssistantConfig(base_url="http://supervisor/core", token="supervisor-token")
    )
    session = Session()
    client.session = session
    states, error = client.fetch(
        (EntityConfig("camera.alfresco", "Alfresco", style="image"),)
    )

    assert error == ""
    assert states[0].available
    camera_call = session.calls[1]
    assert camera_call[0] == (
        "http://supervisor/core/api/camera_proxy/camera.alfresco"
        "?token=signed-camera-token"
    )
    assert camera_call[1]["headers"]["Authorization"] == "Bearer supervisor-token"


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
        assert "Fleet health v0.20" in doubled_ingress.text
        assert "data-resend-screen" in doubled_ingress.text
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


def test_dashboard_studio_builds_standalone_name_card_and_qr_code(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
    )
    profile = {
        "pages": [
            {
                "title": "ID PASS",
                "layout": "rows",
                "entities": [
                    {
                        "entity_id": "static.id_card",
                        "label": "Alex Morgan",
                        "unit": "LDCS · Visitor 042",
                        "icon": "home",
                        "style": "name_card",
                        "source": "static",
                        "value": "Showroom Guest",
                    },
                    {
                        "entity_id": "static.id_qr",
                        "label": "Visitor details",
                        "unit": "SCAN ME",
                        "style": "qr",
                        "source": "static",
                        "value": "https://example.test/visitor/042",
                    },
                ],
            }
        ]
    }

    with TestClient(create_app(config)) as client:
        studio = client.get("/studio/")
        assert "Fixed content (no HA entity)" in studio.text
        assert "Name card / ID pass" in studio.text
        assert "Profile picture" in studio.text
        assert "Bold band" in studio.text
        assert "LinkedIn profile" in studio.text
        assert 'id="addQrTile"' in studio.text

        portrait = io.BytesIO()
        photo = Image.new("RGB", (300, 420), "white")
        for y in range(photo.height):
            shade = round(255 * y / photo.height)
            for x in range(photo.width):
                photo.putpixel((x, y), (shade, shade, shade))
        photo.save(portrait, format="JPEG", quality=90)
        uploaded = client.post(
            "/api/v1/studio/assets/profile-photo",
            content=portrait.getvalue(),
            headers={
                "Content-Type": "image/jpeg",
                "X-FlexDisplay-Filename": "alex-profile.jpg",
            },
        )
        assert uploaded.status_code == 200
        asset = uploaded.json()["asset"]
        assert len(asset["id"]) == 24
        assert asset["filename"] == "alex-profile.jpg"
        assert (tmp_path / "dashboard-assets" / f"{asset['id']}.png").is_file()
        profile["pages"][0]["entities"][0].update(
            {
                "badge_theme": "bold",
                "badge_photo_id": asset["id"],
                "badge_photo_filename": asset["filename"],
            }
        )

        saved = client.put("/api/v1/studio/profiles/visitor", json=profile)
        assert saved.status_code == 200
        tiles = saved.json()["profile"]["pages"][0]["entities"]
        assert tiles[0]["source"] == "static"
        assert tiles[0]["value"] == "Showroom Guest"
        assert tiles[0]["badge_theme"] == "bold"
        assert tiles[0]["badge_photo_id"] == asset["id"]
        assert tiles[0]["badge_photo_filename"] == "alex-profile.jpg"
        assert tiles[1]["value"] == "https://example.test/visitor/042"

        rendered_previews = {}
        for model, expected_size in (("X3", (528, 792)), ("X4", (480, 800))):
            preview = client.post(
                "/api/v1/studio/preview",
                json={"model": model, "profile": profile, "page_index": 0},
            )
            assert preview.status_code == 200
            assert preview.headers["x-flexdisplay-preview-ha-error"] == "false"
            rendered_previews[model] = preview.content
            with Image.open(io.BytesIO(preview.content)) as image:
                assert image.size == expected_size
                assert image.mode == "1"
                assert image.getextrema() == (0, 255)

        without_photo = json.loads(json.dumps(profile))
        without_photo["pages"][0]["entities"][0]["badge_photo_id"] = ""
        without_photo["pages"][0]["entities"][0]["badge_photo_filename"] = ""
        fallback_preview = client.post(
            "/api/v1/studio/preview",
            json={"model": "X4", "profile": without_photo, "page_index": 0},
        )
        assert fallback_preview.status_code == 200
        assert fallback_preview.content != rendered_previews["X4"]

        invalid_upload = client.post(
            "/api/v1/studio/assets/profile-photo",
            content=b"not an image",
            headers={"X-FlexDisplay-Filename": "portrait.txt"},
        )
        assert invalid_upload.status_code == 400


def test_dashboard_studio_offers_qr_page_template_and_common_payload_types(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
    )
    payloads = {
        "website": "https://example.test/visitor",
        "linkedin": "https://www.linkedin.com/in/alex-morgan",
        "text": "Welcome to the LDCS showroom",
        "wifi": "WIFI:T:WPA;S:LDCS Showroom;P:ldcsldcs;H:false;;",
        "contact": (
            "BEGIN:VCARD\nVERSION:3.0\nFN:Alex Morgan\nORG:LDCS\n"
            "TITLE:Visitor\nEMAIL:alex@example.test\nEND:VCARD"
        ),
        "email": "mailto:hello@example.test?subject=Showroom%20visit",
        "phone": "tel:+61300000000",
    }

    with TestClient(create_app(config)) as client:
        studio = client.get("/studio/")
        assert '<option value="qr_code">QR code page</option>' in studio.text
        assert 'title: "QR CODE", layout: "single"' in studio.text
        assert "function buildQrPayload" in studio.text
        assert ".static-fields[hidden]" in studio.text
        for label in (
            "Website link",
            "LinkedIn profile",
            "Plain text",
            "Wi-Fi network",
            "Contact card",
            "Email message",
            "Phone number",
        ):
            assert label in studio.text

        for qr_type, value in payloads.items():
            profile = {
                "pages": [
                    {
                        "title": qr_type,
                        "layout": "single",
                        "entities": [
                            {
                                "entity_id": f"static.qr_{qr_type}",
                                "label": "Scan me",
                                "unit": "SCAN",
                                "style": "qr",
                                "source": "static",
                                "value": value,
                            }
                        ],
                    }
                ]
            }
            saved = client.put(
                f"/api/v1/studio/profiles/qr_{qr_type}",
                json=profile,
            )
            assert saved.status_code == 200
            assert saved.json()["profile"]["pages"][0]["entities"][0]["value"] == value

            preview = client.post(
                "/api/v1/studio/preview",
                json={"model": "X3", "profile": profile, "page_index": 0},
            )
            assert preview.status_code == 200
            with Image.open(io.BytesIO(preview.content)) as image:
                assert image.size == (528, 792)
                assert image.mode == "1"
                assert image.getextrema() == (0, 255)


def test_static_entities_do_not_call_home_assistant() -> None:
    client = HomeAssistantClient(HomeAssistantConfig(token=""))
    states, error = client.fetch(
        (
            EntityConfig(
                "static.message",
                "Welcome",
                source="static",
                value="Clinton Marshall",
            ),
        )
    )

    assert error == ""
    assert states[0].available is True
    assert states[0].state == "Clinton Marshall"


def test_photo_frame_media_pipeline_uploads_converts_and_assigns_albums(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
    )
    formats = {
        "portrait.jpg": "JPEG",
        "graphic.png": "PNG",
        "artwork.webp": "WEBP",
        "legacy.bmp": "BMP",
    }

    with TestClient(create_app(config)) as client:
        album = client.put(
            "/api/v1/photo-frame/albums/family",
            json={
                "name": "Family",
                "shuffle": True,
                "interval_seconds": 1800,
                "start": "07:00",
                "end": "22:00",
                "timezone": "Australia/Melbourne",
            },
        )
        assert album.status_code == 200

        uploaded_ids: list[str] = []
        for index, (filename, image_format) in enumerate(formats.items()):
            source = io.BytesIO()
            Image.new("RGB", (80 + index * 10, 120), (40 * index, 100, 190)).save(
                source,
                format=image_format,
            )
            uploaded = client.post(
                "/api/v1/photo-frame/albums/family/images",
                content=source.getvalue(),
                headers={
                    "Content-Type": f"image/{image_format.lower()}",
                    "X-FlexDisplay-Filename": filename,
                    "X-FlexDisplay-Caption": f"Photo {index + 1}",
                },
            )
            assert uploaded.status_code == 200
            uploaded_ids.append(uploaded.json()["image"]["id"])

        library = client.get("/api/v1/photo-frame").json()
        assert library["capabilities"]["formats"] == ["JPEG", "PNG", "WebP", "BMP"]
        assert len(library["albums"]["family"]["items"]) == 4
        assert {item["filename"] for item in library["albums"]["family"]["items"]} == set(
            formats
        )

        updated = client.put(
            f"/api/v1/photo-frame/albums/family/images/{uploaded_ids[0]}",
            json={"caption": "Portrait caption", "fit": "contain", "rotation": 90},
        )
        assert updated.status_code == 200
        assert updated.json()["image"]["rotation"] == 90

        preview = client.get(
            f"/api/v1/photo-frame/albums/family/images/{uploaded_ids[0]}/preview",
            params={"model": "X3"},
        )
        assert preview.status_code == 200
        with Image.open(io.BytesIO(preview.content)) as rendered:
            assert rendered.size == (528, 792)
            assert rendered.mode == "1"

        assigned = client.put(
            "/api/v1/photo-frame/devices/X4-PHOTO1",
            json={"album_id": "family"},
        )
        assert assigned.status_code == 200
        assert assigned.json()["album_id"] == "family"

        frame = client.get(
            "/api/v1/photo-frame/devices/X4-PHOTO1/image",
            headers={
                "X-FlexDisplay-Width": "480",
                "X-FlexDisplay-Height": "800",
            },
        )
        assert frame.status_code == 200
        assert frame.headers["content-type"] == "image/bmp"
        assert frame.headers["x-flexdisplay-photo-album"] == "family"
        assert frame.headers["x-flexdisplay-photo-count"] == "4"
        with Image.open(io.BytesIO(frame.content)) as rendered:
            assert rendered.size == (480, 800)
            assert rendered.mode == "1"

        device = client.get("/api/v1/devices/X4-PHOTO1").json()
        assert device["assigned_mode"] == "photo_frame"
        assert device["pending_commands"] == ["refresh"]

        fleet_frame = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X4-PHOTO1",
                "X-FlexDisplay-Width": "480",
                "X-FlexDisplay-Height": "800",
                "X-FlexDisplay-Mode": "photo_frame",
                "X-FlexDisplay-SD-Ready": "true",
            },
        )
        assert fleet_frame.status_code == 200
        assert fleet_frame.headers["content-type"] == "image/bmp"
        assert fleet_frame.headers["x-flexdisplay-assigned-mode"] == "photo_frame"
        assert fleet_frame.headers["x-flexdisplay-photo-album"] == "family"
        with Image.open(io.BytesIO(fleet_frame.content)) as rendered:
            assert rendered.size == (480, 800)
            assert rendered.mode == "1"

        too_large = client.post(
            "/api/v1/photo-frame/albums/family/images",
            content=b"x" * (8 * 1024 * 1024 + 1),
            headers={"X-FlexDisplay-Filename": "too-large.jpg"},
        )
        assert too_large.status_code == 413

    with TestClient(create_app(config)) as client:
        persisted = client.get("/api/v1/photo-frame").json()
        assert persisted["albums"]["family"]["name"] == "Family"
        assert len(persisted["albums"]["family"]["items"]) == 4


def test_dashboard_studio_exposes_photo_frame_media_library(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
    )
    with TestClient(create_app(config)) as client:
        html = client.get("/studio/").text

    assert "Photo Frame v0.18" in html
    assert 'accept="image/jpeg,image/png,image/webp,image/bmp"' in html
    assert "function loadPhotoLibrary" in html
    assert "function uploadPhotos" in html
    assert "function importPhotoFromHomeAssistant" in html


def test_photo_frame_schedule_pauses_rotation_and_wakes_for_next_window(
    tmp_path: Path,
) -> None:
    store = PhotoFrameMediaStore(tmp_path / "photo-frame.json")
    store.put_album(
        "scheduled",
        {
            "name": "Scheduled",
            "interval_seconds": 600,
            "start": "08:00",
            "end": "20:00",
            "timezone": "UTC",
        },
    )
    for colour, filename in (((20, 60, 100), "one.png"), ((200, 160, 80), "two.png")):
        source = io.BytesIO()
        Image.new("RGB", (80, 120), colour).save(source, format="PNG")
        store.add_image("scheduled", source.getvalue(), filename=filename)
    store.assign("X3-SCHEDULE", "scheduled")

    _, before_window = store.next_for_device(
        "X3-SCHEDULE",
        width=528,
        height=792,
        now=datetime(2026, 7, 27, 7, 0, tzinfo=UTC),
    )
    assert before_window["X-FlexDisplay-Photo-Active"] == "false"
    assert before_window["X-FlexDisplay-Photo-Index"] == "0"
    assert before_window["X-FlexDisplay-Refresh-Interval"] == "3600"
    assert before_window["X-FlexDisplay-Photo-Start"] == "08:00"
    assert before_window["X-FlexDisplay-Photo-End"] == "20:00"
    assert before_window["X-FlexDisplay-Photo-Timezone"] == "UTC"

    _, active_window = store.next_for_device(
        "X3-SCHEDULE",
        width=528,
        height=792,
        now=datetime(2026, 7, 27, 8, 0, tzinfo=UTC),
    )
    assert active_window["X-FlexDisplay-Photo-Active"] == "true"
    assert active_window["X-FlexDisplay-Photo-Index"] == "1"
    assert active_window["X-FlexDisplay-Refresh-Interval"] == "600"

    _, after_window = store.next_for_device(
        "X3-SCHEDULE",
        width=528,
        height=792,
        now=datetime(2026, 7, 27, 22, 0, tzinfo=UTC),
    )
    assert after_window["X-FlexDisplay-Photo-Active"] == "false"
    assert after_window["X-FlexDisplay-Photo-Index"] == "1"
    assert after_window["X-FlexDisplay-Refresh-Interval"] == "36000"


def test_photo_frame_empty_album_renders_setup_screen_instead_of_ha_error(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        home_assistant=HomeAssistantConfig(token=""),
    )
    with TestClient(create_app(config)) as client:
        assigned = client.put(
            "/api/v1/photo-frame/devices/X3-EMPTY",
            json={"album_id": "default"},
        )
        assert assigned.status_code == 200

        response = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-EMPTY",
                "X-FlexDisplay-Width": "528",
                "X-FlexDisplay-Height": "792",
                "X-FlexDisplay-Mode": "photo_frame",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.headers["x-flexdisplay-photo-count"] == "0"
    assert response.headers["x-flexdisplay-photo-filename"] == "No photos"
    with Image.open(io.BytesIO(response.content)) as rendered:
        assert rendered.size == (528, 792)
        assert rendered.mode == "1"


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
        dynamic_name_card = client.put(
            "/api/v1/studio/profiles/wall",
            json={
                "pages": [
                    {
                        "title": "Pass",
                        "entities": [
                            {
                                "entity_id": "sensor.person",
                                "label": "Person",
                                "style": "name_card",
                                "source": "home_assistant",
                            }
                        ],
                    }
                ]
            },
        )
        assert dynamic_name_card.status_code == 400
        invalid_badge_theme = client.put(
            "/api/v1/studio/profiles/wall",
            json={
                "pages": [
                    {
                        "title": "Pass",
                        "entities": [
                            {
                                "entity_id": "static.person",
                                "label": "Person",
                                "style": "name_card",
                                "source": "static",
                                "value": "Visitor",
                                "badge_theme": "low-contrast-rainbow",
                            }
                        ],
                    }
                ]
            },
        )
        assert invalid_badge_theme.status_code == 400


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
    for style in ("value", "gauge", "progress", "history", "qr", "name_card"):
        entity = EntityState(
            "sensor.visual",
            style.title(),
            "https://example.test" if style == "qr" else "Engineer" if style == "name_card" else "64",
            "LDCS" if style == "name_card" else "" if style == "qr" else "%",
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
        assert (
            screen.headers["x-flexdisplay-firmware-url"]
            == "http://testserver/api/v1/firmware/current.bin"
        )
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
        assert ready["usb_recovery_verification_ready"] is False
        assert ready["firmware_update_status"] == "verified"
        assert ready["firmware_verification_method"] == "device_checkin"
        assert ready["firmware_rollout_status"] == "canary_verified"

        unauthenticated = client.post(
            "/api/v1/devices/X4-USB001/firmware/verify-usb-recovery",
            json={
                "expected_target_version": firmware.version,
                "expected_command_id": command_id,
            },
        )
        assert unauthenticated.status_code == 401

        already_verified = client.post(
            "/api/v1/devices/X4-USB001/firmware/verify-usb-recovery",
            headers=authorized,
            json={
                "expected_target_version": firmware.version,
                "expected_command_id": "X4-USB001-wrong",
            },
        )
        assert already_verified.status_code == 409
        assert "already verified" in already_verified.json()["detail"]

        record = client.get("/api/v1/devices/X4-USB001").json()
        assert record["firmware_update_status"] == "verified"
        assert record["firmware_verification_method"] == "device_checkin"
        assert record["firmware_rollout_status"] == "canary_verified"
        assert record["firmware_canary_verified"] is True
        assert record["dispatched_commands"] == []
        assert record.get("dispatched_command_id") is None
        assert record["last_command_result"] == "install:boot-reconciled"
        assert record["usb_recovery_verification_ready"] is False


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
        assert ready["usb_recovery_verification_ready"] is False
        record = ready
        assert record["firmware_update_status"] == "verified"
        assert record["firmware_verification_method"] == "device_checkin"
        assert record["firmware_rollout_status"] == "fleet_active"
        assert record["firmware_canary_device_id"] == "X4-CANARY"
        assert record["dispatched_commands"] == []
        assert record["last_command_id"] == fleet_command_id
        assert record["last_command_result"] == "install:boot-reconciled"


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


def test_cancel_stops_dispatched_install_and_device_retry(tmp_path: Path) -> None:
    firmware = FirmwareConfig(
        version="1.4.1-flexdisplay.0.19.0",
        url="https://example.test/firmware.bin",
        sha256="ab" * 32,
        size=5_500_000,
        mirror_enabled=False,
    )
    config = BridgeConfig(state_path=tmp_path / "state.json", firmware=firmware)
    with TestClient(create_app(config)) as client:
        telemetry = {
            "X-FlexDisplay-ID": "X4-CANCEL",
            "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.18.0",
            "X-FlexDisplay-SD-Ready": "true",
            "X-FlexDisplay-USB-Connected": "true",
        }
        client.get("/api/v1/screen", headers=telemetry)
        assert client.post("/api/v1/devices/X4-CANCEL/commands/install").status_code == 200
        delivery = client.get("/api/v1/screen", headers=telemetry)
        command_id = delivery.headers["x-flexdisplay-command-id"]

        cancelled = client.delete("/api/v1/devices/X4-CANCEL/commands")
        assert cancelled.status_code == 200
        assert cancelled.json()["device"]["firmware_update_status"] == "cancelled"
        assert cancelled.json()["device"]["dispatched_commands"] == []

        progress = client.get(
            "/api/v1/devices/X4-CANCEL/firmware/progress",
            headers={
                "X-FlexDisplay-ID": "X4-CANCEL",
                "X-FlexDisplay-Command-ID": command_id,
                "X-FlexDisplay-Firmware-Stage": "validating",
                "X-FlexDisplay-Firmware-Percent": "65",
            },
        )
        assert progress.status_code == 200
        assert progress.json()["cancel_requested"] is True
        assert "install" not in client.get(
            "/api/v1/screen", headers=telemetry
        ).headers.get("x-flexdisplay-commands", "")


def test_failed_rollout_can_reset_and_retry_with_backoff(tmp_path: Path) -> None:
    firmware = FirmwareConfig(
        version="1.4.1-flexdisplay.0.19.0",
        url="https://example.test/firmware.bin",
        sha256="ab" * 32,
        size=5_500_000,
        retry_limit=1,
        retry_backoff_seconds=0,
        mirror_enabled=False,
    )
    config = BridgeConfig(state_path=tmp_path / "state.json", firmware=firmware)
    with TestClient(create_app(config)) as client:
        telemetry = {
            "X-FlexDisplay-ID": "X3-RETRY1",
            "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.18.0",
            "X-FlexDisplay-SD-Ready": "true",
            "X-FlexDisplay-USB-Connected": "true",
        }
        client.get("/api/v1/screen", headers=telemetry)
        client.post("/api/v1/devices/X3-RETRY1/commands/install")
        first = client.get("/api/v1/screen", headers=telemetry)
        client.get(
            "/api/v1/screen",
            headers={
                **telemetry,
                "X-FlexDisplay-Command-ID": first.headers["x-flexdisplay-command-id"],
                "X-FlexDisplay-Command-Result": "install:download-failed",
            },
        )
        failed = client.get("/api/v1/devices/X3-RETRY1").json()
        assert failed["firmware_rollout_status"] == "failed"
        assert failed["firmware_update_error_at"]

        retried = client.post("/api/v1/devices/X3-RETRY1/firmware/retry")
        assert retried.status_code == 200
        assert retried.json()["device"]["firmware_retry_count"] == 1
        assert retried.json()["device"]["firmware_update_status"] == "queued"

        client.delete("/api/v1/devices/X3-RETRY1/commands")
        limited = client.post("/api/v1/devices/X3-RETRY1/firmware/retry")
        assert limited.status_code == 409
        assert "retry limit" in limited.json()["detail"]

        reset = client.post("/api/v1/firmware/rollout/reset")
        assert reset.status_code == 200
        assert reset.json()["rollout"]["status"] == "awaiting_canary"
        record = client.get("/api/v1/devices/X3-RETRY1").json()
        assert record["pending_commands"] == []
        assert record["dispatched_commands"] == []


def test_exact_target_usb_checkin_auto_reconciles_failed_update(tmp_path: Path) -> None:
    firmware = FirmwareConfig(
        version="1.4.1-flexdisplay.0.19.0",
        url="https://example.test/firmware.bin",
        sha256="ab" * 32,
        size=5_500_000,
        mirror_enabled=False,
    )
    config = BridgeConfig(state_path=tmp_path / "state.json", firmware=firmware)
    with TestClient(create_app(config)) as client:
        telemetry = {
            "X-FlexDisplay-ID": "X4-RECOVER",
            "X-FlexDisplay-Firmware": "1.4.1-flexdisplay.0.18.0",
            "X-FlexDisplay-SD-Ready": "true",
            "X-FlexDisplay-USB-Connected": "true",
        }
        client.get("/api/v1/screen", headers=telemetry)
        client.post("/api/v1/devices/X4-RECOVER/commands/install")
        delivery = client.get("/api/v1/screen", headers=telemetry)
        client.get(
            "/api/v1/screen",
            headers={
                **telemetry,
                "X-FlexDisplay-Command-ID": delivery.headers[
                    "x-flexdisplay-command-id"
                ],
                "X-FlexDisplay-Command-Result": "install:download-failed",
            },
        )
        client.get(
            "/api/v1/screen",
            headers={
                **telemetry,
                "X-FlexDisplay-Firmware": firmware.version,
            },
        )
        recovered = client.get("/api/v1/devices/X4-RECOVER").json()
        assert recovered["firmware_update_status"] == "verified"
        assert recovered["firmware_update_stage"] == "verified"
        assert recovered["firmware_update_percent"] == 100
        assert recovered["firmware_verification_method"] == "device_checkin"
        assert recovered["firmware_rollout_status"] == "canary_verified"
        assert recovered["update_available"] is False


def test_firmware_mirror_downloads_verifies_and_serves_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    payload = b"firmware-image" * 8192
    digest = hashlib.sha256(payload).hexdigest()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            del chunk_size
            yield payload

    monkeypatch.setattr(
        "flexdisplay_bridge.firmware_mirror.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )
    firmware = FirmwareConfig(
        version="1.4.1-flexdisplay.0.19.0",
        url="https://example.test/firmware.bin",
        sha256=digest,
        size=len(payload),
        mirror_enabled=True,
    )
    config = BridgeConfig(state_path=tmp_path / "state.json", firmware=firmware)
    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/firmware/current.bin")
        assert response.status_code == 200
        assert response.content == payload
        assert response.headers["x-flexdisplay-firmware-sha256"] == digest
        health = client.get("/healthz").json()
        assert health["firmware_mirror"]["ready"] is True
