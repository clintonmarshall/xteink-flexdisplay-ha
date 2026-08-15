from __future__ import annotations

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from flexdisplay_bridge.app import CameraSnapshotBroker, create_app
from flexdisplay_bridge.config import (
    BridgeConfig,
    DashboardPageConfig,
    DashboardProfileConfig,
    DeviceConfig,
    EntityConfig,
    MqttConfig,
)
from flexdisplay_bridge.device_capabilities import resolve_device_capabilities
from flexdisplay_bridge.mqtt_service import MqttService
from flexdisplay_bridge.home_assistant import EntityState, HomeAssistantClient
from flexdisplay_bridge.voice_assistant import (
    MAX_AUDIO_BYTES,
    HomeAssistantVoiceClient,
    VoiceAssistantResult,
)
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
    config = replace(_config(tmp_path), api_key="bridge-secret")
    with TestClient(create_app(config)) as client:
        response = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "ROOK-TEST01",
                "X-FlexDisplay-Model": "ROOK",
                "X-FlexDisplay-Receiver-Token": "test-receiver-token-01",
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

        device = client.get(
            "/api/v1/devices/ROOK-TEST01",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
        ).json()
        assert device["model"] == "ROOK"
        assert device["display_shape"] == "round"
        assert device["touch_available"] is True
        assert device["color_available"] is True
        assert device["client_platform"] == "android"
        assert device["display_technology"] == "lcd"
        assert device["power_class"] == "always_on_color"
        assert device["refresh_delivery"] == "long_poll"
        assert device["policy_overlay"] == "always_on_color"
        assert device["assigned_refresh_interval_seconds"] == 60
        assert device["assigned_live_mode"] is True
        assert device["assigned_intelligent_sleep"] is False
        assert device["health_state"] == "healthy"
        assert device["health_issues"] == []
        assert device["consecutive_sd_failures"] == 0
        assert device["sd_failure_events"] == 0


def test_rook_refresh_command_wakes_receiver_long_poll(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-secret",
        profiles={"spot": DashboardProfileConfig(name="spot")},
        default_profile="spot",
    )
    receiver_headers = {
        "X-FlexDisplay-ID": "ROOK-WAKE01",
        "X-FlexDisplay-Model": "ROOK",
        "X-FlexDisplay-Capabilities": "android,color,round-display,notifications",
        "X-FlexDisplay-Receiver-Token": "receiver-secret",
    }
    with TestClient(create_app(config)) as client:
        screen = client.get("/api/v1/screen", headers=receiver_headers)
        assert screen.headers["x-flexdisplay-refresh-interval"] == "60"
        assert screen.headers["x-flexdisplay-live-mode"] == "true"
        assert screen.headers["x-flexdisplay-sleep-action"] == "awake"

        queued = client.post(
            "/api/v1/devices/ROOK-WAKE01/commands/refresh",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
        )
        assert queued.status_code == 200
        event = client.get(
            "/api/v1/devices/ROOK-WAKE01/notifications/next?after=0&timeout=0",
            headers={"X-FlexDisplay-Receiver-Token": "receiver-secret"},
        ).json()

        assert event["event"] == "screen_refresh"
        assert event["refresh"] is True
        assert event["reason"] == "command:refresh"
        assert event["notification"] is None
        assert event["sequence"] > 0

        applied = client.put(
            "/api/v1/fleet/policy",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
            json={
                "profile": "battery_saver",
                "scope": "devices",
                "device_ids": ["ROOK-WAKE01"],
                "delivery": "apply_now",
            },
        )
        assert applied.status_code == 200
        refreshed = client.get("/api/v1/screen", headers=receiver_headers)
        assert refreshed.headers["x-flexdisplay-refresh-interval"] == "60"
        assert refreshed.headers["x-flexdisplay-live-mode"] == "true"
        assert refreshed.headers["x-flexdisplay-sleep-action"] == "awake"
        device = client.get(
            "/api/v1/devices/ROOK-WAKE01",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
        ).json()
        assert device["assigned_policy_name"] == "battery_saver"
        assert device["policy_overlay"] == "always_on_color"
        assert device["assigned_intelligent_sleep"] is False


def test_rook_cannot_receive_esp32_firmware(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "ROOK-TEST02",
                "X-FlexDisplay-Model": "ROOK",
                "X-FlexDisplay-Receiver-Token": "test-receiver-token-02",
                "X-FlexDisplay-Width": "480",
                "X-FlexDisplay-Height": "480",
                "X-FlexDisplay-Capabilities": "android,round-display",
            },
        )
        response = client.post("/api/v1/devices/ROOK-TEST02/commands/install")

        assert response.status_code == 409
        assert response.json()["detail"] == "No firmware release is configured"


def test_checkers_screen_is_landscape_android_png(tmp_path: Path) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    with TestClient(create_app(config)) as client:
        response = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "CHECKERS-SHOW501",
                "X-FlexDisplay-Model": "CHECKERS",
                "X-FlexDisplay-Receiver-Token": "test-receiver-token-03",
                "X-FlexDisplay-Firmware": "android-0.2.0",
                "X-FlexDisplay-Width": "960",
                "X-FlexDisplay-Height": "480",
                "X-FlexDisplay-Capabilities": "android,color,touch,png,empty-unchanged,kiosk,interactions,notifications,audio",
                "X-FlexDisplay-SD-Ready": "false",
            },
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert "X-FlexDisplay-Latest-Firmware" not in response.headers
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (960, 480)

        device = client.get(
            "/api/v1/devices/CHECKERS-SHOW501", headers=management
        ).json()
        assert device["model"] == "CHECKERS"
        assert device["display_shape"] == "rectangular"
        assert device["touch_available"] is True
        assert device["color_available"] is True
        assert device["client_platform"] == "android"

        firmware = client.post(
            "/api/v1/devices/CHECKERS-SHOW501/commands/install", headers=management
        )
        assert firmware.status_code == 409
        assert firmware.json()["detail"] == "No firmware release is configured"


def test_android_receiver_fleet_controls_and_diagnostics(tmp_path: Path) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    headers = {
        "X-FlexDisplay-ID": "CHECKERS-CONTROL01",
        "X-FlexDisplay-Model": "CHECKERS",
        "X-FlexDisplay-Receiver-Token": "test-receiver-token-04",
        "X-FlexDisplay-Firmware": "android-0.4.0",
        "X-FlexDisplay-Width": "960",
        "X-FlexDisplay-Height": "480",
        "X-FlexDisplay-Capabilities": "android,color,touch,png,audio,assist",
        "X-FlexDisplay-Camera-Available": "true",
        "X-FlexDisplay-Microphone-Available": "true",
        "X-FlexDisplay-Audio-Available": "true",
        "X-FlexDisplay-Touch-Available": "true",
        "X-FlexDisplay-Always-On": "true",
        "X-FlexDisplay-Device-Class": "echo_show_5",
        "X-FlexDisplay-Volume": "55",
        "X-FlexDisplay-Muted": "false",
        "X-FlexDisplay-Brightness": "72",
    }
    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/screen", headers=headers)
        assert response.status_code == 200
        device = client.get(
            "/api/v1/devices/CHECKERS-CONTROL01",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
        ).json()
        assert device["voice_volume"] == 55
        assert device["voice_muted"] is False
        assert device["screen_brightness"] == 72
        assert device["camera_available"] is True
        assert device["microphone_available"] is True
        assert device["audio_available"] is True
        assert device["touch_available"] is True
        assert device["always_on_available"] is True
        assert device["device_class"] == "echo_show_5"
        assert device["screen_resolution"] == "960x480"
        assert device["display_technology"] == "lcd"
        assert device["power_class"] == "always_on_color"
        assert device["refresh_delivery"] == "long_poll"

        voice = client.put(
            "/api/v1/devices/CHECKERS-CONTROL01/voice",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
            json={"volume": 35, "muted": True},
        )
        assert voice.status_code == 200
        display = client.put(
            "/api/v1/devices/CHECKERS-CONTROL01/display",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
            json={"brightness": 40},
        )
        assert display.status_code == 200
        command = client.post(
            "/api/v1/devices/CHECKERS-CONTROL01/commands/test-chime",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
        )
        assert command.status_code == 200

        update = client.get("/api/v1/screen", headers=headers)
        assert update.headers["x-flexdisplay-desired-volume"] == "35"
        assert update.headers["x-flexdisplay-desired-muted"] == "true"
        assert update.headers["x-flexdisplay-desired-brightness"] == "40"
        assert update.headers["x-flexdisplay-commands"] == "test-chime"


def _android_phone_headers(token: str = "phone-receiver-secret") -> dict[str, str]:
    return {
        "X-FlexDisplay-ID": "ANDROID-PHONE01",
        "X-FlexDisplay-Model": "ANDROID_PHONE",
        "X-FlexDisplay-Firmware": "android-0.5.0",
        "X-FlexDisplay-Width": "1080",
        "X-FlexDisplay-Height": "2400",
        "X-FlexDisplay-Capabilities": (
            "android,companion,color,touch,png,camera,microphone,speaker,battery,usb"
        ),
        "X-FlexDisplay-Receiver-Token": token,
        "X-FlexDisplay-Camera-Available": "true",
        "X-FlexDisplay-Camera-Permission": "true",
        "X-FlexDisplay-Camera-Policy": "allow_while_open",
        "X-FlexDisplay-Foreground-Active": "true",
        "X-FlexDisplay-Foreground-Session": "0123456789abcdef",
        "X-FlexDisplay-Microphone-Available": "true",
        "X-FlexDisplay-Microphone-Permission": "true",
        "X-FlexDisplay-Speaker-Available": "true",
        "X-FlexDisplay-Battery-Percent": "76",
        "X-FlexDisplay-Battery-Charging": "false",
        "X-FlexDisplay-Battery-Status": "discharging",
        "X-FlexDisplay-Battery-Health": "good",
        "X-FlexDisplay-Battery-Temperature-C": "28.5",
        "X-FlexDisplay-Battery-Voltage-MV": "4200",
        "X-FlexDisplay-Battery-Plug-Type": "none",
        "X-FlexDisplay-Battery-Current-MA": "-240",
        "X-FlexDisplay-USB-Connected": "false",
        "X-FlexDisplay-Hardware-Manufacturer": "Samsung",
        "X-FlexDisplay-Hardware-Model": "SM-G991B",
    }


def test_android_phone_token_is_pinned_before_any_state_or_command_mutation(
    tmp_path: Path,
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    with TestClient(create_app(config)) as client:
        paired = client.get("/api/v1/screen", headers=_android_phone_headers())
        assert paired.status_code == 200
        before = client.app.state.store.get("ANDROID-PHONE01")
        queued = client.post(
            "/api/v1/devices/ANDROID-PHONE01/commands/test-chime",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
        )
        assert queued.status_code == 200

        attacker = _android_phone_headers("attacker-receiver-secret")
        attacker["X-FlexDisplay-Model"] = "X3"
        attacker["X-FlexDisplay-Command-Result"] = "test-chime:ok"
        response = client.get("/api/v1/screen", headers=attacker)

        assert response.status_code == 401
        after = client.app.state.store.get("ANDROID-PHONE01")
        assert after["model"] == "ANDROID_PHONE"
        assert after["last_seen"] == before["last_seen"]
        assert after["pending_commands"] == ["test-chime"]


def test_android_phone_first_token_wins_and_loser_cannot_repair(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        winner = client.get(
            "/api/v1/screen", headers=_android_phone_headers("first-phone-token")
        )
        assert winner.status_code == 200
        loser = client.get(
            "/api/v1/screen", headers=_android_phone_headers("second-phone-token")
        )
        assert loser.status_code == 401
        winner_again = client.get(
            "/api/v1/screen", headers=_android_phone_headers("first-phone-token")
        )
        assert winner_again.status_code == 200


def test_sensitive_device_inventory_requires_configured_correct_key(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        assert client.get("/api/v1/devices").status_code == 503
        assert client.get("/healthz").status_code == 200

    config = replace(_config(tmp_path / "keyed"), api_key="bridge-secret")
    with TestClient(create_app(config)) as client:
        assert client.get("/api/v1/devices").status_code == 401
        assert client.get(
            "/api/v1/devices",
            headers={"X-FlexDisplay-Bridge-Key": "wrong"},
        ).status_code == 401
        assert client.get(
            "/api/v1/devices",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
        ).status_code == 200


def test_android_battery_and_privacy_telemetry_replace_stale_values(
    tmp_path: Path,
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    with TestClient(create_app(config)) as client:
        headers = _android_phone_headers()
        headers.update(
            {
                "X-FlexDisplay-Battery-Status": "not_charging",
                "X-FlexDisplay-Battery-Plug-Type": "usb",
                "X-FlexDisplay-USB-Connected": "true",
                "X-FlexDisplay-Dock-Enabled": "true",
                "X-FlexDisplay-Dock-Active": "true",
            }
        )
        assert client.get("/api/v1/screen", headers=headers).status_code == 200
        record = client.get(
            "/api/v1/devices/ANDROID-PHONE01", headers=management
        ).json()
        assert record["battery_voltage"] == 4.2
        assert record["battery_voltage_mv"] == 4200
        assert record["usb_connected"] is True
        assert record["battery_charging"] is False
        assert record["dock_active"] is True
        assert record["battery_observed_at"]

        invalid = _android_phone_headers()
        for key in list(invalid):
            if "Battery-" in key or key == "X-FlexDisplay-USB-Connected":
                invalid.pop(key)
        invalid.update(
            {
                "X-FlexDisplay-Battery-Status": "unknown",
                "X-FlexDisplay-Battery-Health": "unknown",
                "X-FlexDisplay-Battery-Voltage-MV": "0",
                "X-FlexDisplay-Battery-Temperature-C": "nan",
                "X-FlexDisplay-Battery-Current-MA": "inf",
                "X-FlexDisplay-Battery-Plug-Type": "unknown",
                "X-FlexDisplay-Camera-Available": "invalid",
                "X-FlexDisplay-Camera-Permission": "invalid",
                "X-FlexDisplay-Speaker-Available": "invalid",
            }
        )
        assert client.get("/api/v1/screen", headers=invalid).status_code == 200
        record = client.get(
            "/api/v1/devices/ANDROID-PHONE01", headers=management
        ).json()
        for field in (
            "battery_percent",
            "battery_voltage",
            "battery_voltage_mv",
            "battery_status",
            "battery_health",
            "battery_temperature_c",
            "battery_current_ma",
            "battery_plug_type",
            "battery_observed_at",
            "usb_connected",
            "camera_available",
            "camera_permission",
            "speaker_available",
        ):
            assert field not in record

        active = _android_phone_headers()
        active.update(
            {
                "X-FlexDisplay-Battery-Plug-Type": "usb",
                "X-FlexDisplay-USB-Connected": "true",
                "X-FlexDisplay-Dock-Enabled": "true",
                "X-FlexDisplay-Dock-Active": "true",
            }
        )
        client.get("/api/v1/screen", headers=active)
        client.app.state.store.update_metadata(
            "ANDROID-PHONE01", {"last_seen": "2000-01-01T00:00:00+00:00"}
        )
        stale = client.get(
            "/api/v1/devices/ANDROID-PHONE01", headers=management
        ).json()
        assert stale["online"] is False
        assert stale["foreground_active"] is False
        assert stale["dock_active"] is False


def test_android_companion_foreground_session_gates_camera_and_assist(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    receiver = {
        "X-FlexDisplay-Receiver-Token": "phone-receiver-secret",
        "Content-Type": "application/octet-stream",
    }
    monkeypatch.setattr(
        HomeAssistantVoiceClient,
        "run",
        lambda self, audio, device_id="": VoiceAssistantResult(
            transcript="turn on the light",
            response_text="Done",
            audio_pcm=b"\x00\x00",
        ),
    )
    with TestClient(create_app(config)) as client:
        background = _android_phone_headers()
        background["X-FlexDisplay-Foreground-Active"] = "false"
        background.pop("X-FlexDisplay-Foreground-Session")
        client.get("/api/v1/screen", headers=background)
        assert client.post(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot/request",
            headers=management,
        ).status_code == 409

        client.get("/api/v1/screen", headers=_android_phone_headers())
        client.put(
            "/api/v1/devices/ANDROID-PHONE01/voice",
            headers=management,
            json={"microphone_enabled": True},
        )
        assert client.post(
            "/api/v1/devices/ANDROID-PHONE01/assist",
            headers={"X-FlexDisplay-Receiver-Token": "phone-receiver-secret"},
            content=b"\x00\x00" * 8000,
        ).status_code == 415
        assert client.post(
            "/api/v1/devices/ANDROID-PHONE01/assist",
            headers=receiver,
            content=b"\x00" * (MAX_AUDIO_BYTES + 1),
        ).status_code == 413
        assisted = client.post(
            "/api/v1/devices/ANDROID-PHONE01/assist",
            headers=receiver,
            content=b"\x00\x00" * 8000,
        )
        assert assisted.status_code == 200
        assert assisted.headers["x-flexdisplay-assist-transcript"] == "turn on the light"

        requested = client.post(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot/request",
            headers=management,
        )
        assert requested.status_code == 200
        resumed = _android_phone_headers()
        resumed["X-FlexDisplay-Foreground-Session"] = "fedcba9876543210"
        response = client.get("/api/v1/screen", headers=resumed)
        assert "camera-snapshot" not in response.headers.get(
            "x-flexdisplay-commands", ""
        )
        record = client.app.state.store.get("ANDROID-PHONE01")
        assert record["last_command_result"] == "camera-snapshot:session-mismatch"

        client.get("/api/v1/screen", headers=background)
        assert client.post(
            "/api/v1/devices/ANDROID-PHONE01/assist",
            headers=receiver,
            content=b"\x00\x00" * 8000,
        ).status_code == 409


def test_android_phone_microphone_requires_explicit_management_opt_in(
    tmp_path: Path,
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    receiver = {"X-FlexDisplay-Receiver-Token": "phone-receiver-secret"}
    with TestClient(create_app(config)) as client:
        paired = client.get("/api/v1/screen", headers=_android_phone_headers())

        assert paired.status_code == 200
        assert paired.headers["x-flexdisplay-desired-microphone-enabled"] == "false"
        assert (
            client.app.state.store.get("ANDROID-PHONE01")[
                "desired_microphone_enabled"
            ]
            is False
        )
        denied = client.post(
            "/api/v1/devices/ANDROID-PHONE01/assist",
            headers=receiver,
            content=b"\x00\x00" * 8000,
        )
        assert denied.status_code == 409
        assert denied.json()["detail"] == "Microphone is disabled"

        for invalid_value in ("false", 1, []):
            invalid = client.put(
                "/api/v1/devices/ANDROID-PHONE01/voice",
                headers=management,
                json={"microphone_enabled": invalid_value},
            )
            assert invalid.status_code == 400
            assert (
                client.app.state.store.get("ANDROID-PHONE01")[
                    "desired_microphone_enabled"
                ]
                is False
            )

        enabled = client.put(
            "/api/v1/devices/ANDROID-PHONE01/voice",
            headers=management,
            json={"microphone_enabled": True},
        )
        assert enabled.status_code == 200
        refreshed = client.get("/api/v1/screen", headers=_android_phone_headers())
        assert refreshed.headers["x-flexdisplay-desired-microphone-enabled"] == "true"


def test_camera_snapshot_requires_explicit_one_time_correlated_request(
    tmp_path: Path,
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    receiver = {"X-FlexDisplay-Receiver-Token": "phone-receiver-secret"}
    jpeg = io.BytesIO()
    Image.new("RGB", (640, 480), "navy").save(jpeg, format="JPEG")
    with TestClient(create_app(config)) as client:
        assert client.get(
            "/api/v1/screen", headers=_android_phone_headers()
        ).status_code == 200

        direct_command = client.post(
            "/api/v1/devices/ANDROID-PHONE01/commands/camera-snapshot",
            headers=management,
        )
        assert direct_command.status_code == 400
        assert direct_command.json()["detail"] == "Unsupported command"

        unsolicited = client.put(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot",
            headers={**receiver, "X-FlexDisplay-Command-ID": "wrong"},
            content=jpeg.getvalue(),
        )
        assert unsolicited.status_code == 409

        requested = client.post(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot/request",
            headers=management,
        )
        assert requested.status_code == 200
        command = client.get(
            "/api/v1/screen", headers=_android_phone_headers()
        )
        command_id = command.headers["x-flexdisplay-command-id"]
        assert command.headers["x-flexdisplay-commands"] == "camera-snapshot"
        assert (
            command.headers["x-flexdisplay-command-foreground-session"]
            == "0123456789abcdef"
        )

        accepted = client.put(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot",
            headers={
                **receiver,
                "Content-Type": "application/octet-stream",
                "X-FlexDisplay-Command-ID": command_id,
                "X-FlexDisplay-Camera-Facing": "front",
            },
            content=jpeg.getvalue(),
        )
        assert accepted.status_code == 200
        first = client.get(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot", headers=management
        )
        assert first.status_code == 200
        assert first.headers["content-type"] == "image/jpeg"
        assert first.headers["cache-control"] == "no-store, private"
        cached = first.content

        replay = client.put(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot",
            headers={
                **receiver,
                "Content-Type": "application/octet-stream",
                "X-FlexDisplay-Command-ID": command_id,
            },
            content=jpeg.getvalue(),
        )
        assert replay.status_code == 409
        assert client.get(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot", headers=management
        ).content == cached


def test_sensitive_camera_management_fails_closed_without_bridge_key(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        assert client.get(
            "/api/v1/screen", headers=_android_phone_headers()
        ).status_code == 200
        response = client.post(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot/request"
        )
        assert response.status_code == 503


def test_camera_snapshot_broker_physically_expires_cached_bytes() -> None:
    broker = CameraSnapshotBroker()
    broker.put(
        "ANDROID-PHONE01",
        b"jpeg-bytes",
        captured_at="2000-01-01T00:00:00+00:00",
        facing="front",
    )

    assert broker.expire(300) == ["ANDROID-PHONE01"]
    assert broker.get("ANDROID-PHONE01") is None


def test_camera_snapshot_metadata_is_cleared_when_bridge_restarts(
    tmp_path: Path,
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    receiver = {
        "X-FlexDisplay-Receiver-Token": "phone-receiver-secret",
        "Content-Type": "application/octet-stream",
    }
    jpeg = io.BytesIO()
    Image.new("RGB", (64, 64), "navy").save(jpeg, format="JPEG")

    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers=_android_phone_headers())
        requested = client.post(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot/request",
            headers=management,
        )
        assert requested.status_code == 200
        command = client.get("/api/v1/screen", headers=_android_phone_headers())
        accepted = client.put(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot",
            headers={
                **receiver,
                "X-FlexDisplay-Command-ID": command.headers[
                    "x-flexdisplay-command-id"
                ],
            },
            content=jpeg.getvalue(),
        )
        assert accepted.status_code == 200
        assert client.app.state.store.get("ANDROID-PHONE01")["camera_snapshot_at"]

    restarted = create_app(config)
    record = restarted.state.store.get("ANDROID-PHONE01")
    assert record is not None
    assert "camera_snapshot_at" not in record
    assert "camera_snapshot_facing" not in record
    assert "camera_snapshot_content_type" not in record
    assert "camera_snapshot_size" not in record
    with TestClient(restarted) as client:
        missing = client.get(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot",
            headers=management,
        )
        assert missing.status_code == 404


def test_camera_snapshot_rejects_oversize_and_expired_dispatch(
    tmp_path: Path,
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    receiver = {
        "X-FlexDisplay-Receiver-Token": "phone-receiver-secret",
        "Content-Type": "application/octet-stream",
    }
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers=_android_phone_headers())
        client.post(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot/request",
            headers=management,
        )
        command = client.get("/api/v1/screen", headers=_android_phone_headers())
        command_id = command.headers["x-flexdisplay-command-id"]

        too_large = client.put(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot",
            headers={
                **receiver,
                "X-FlexDisplay-Command-ID": command_id,
                "Content-Length": str(5 * 1024 * 1024 + 1),
            },
            content=b"",
        )
        assert too_large.status_code == 413

        # The store is in memory, so use its lock-protected metadata helper to
        # age the same active dispatch without changing last_seen.
        client.app.state.store.update_metadata(
            "ANDROID-PHONE01", {"command_dispatched_at": "2000-01-01T00:00:00+00:00"}
        )
        jpeg = io.BytesIO()
        Image.new("RGB", (64, 64), "navy").save(jpeg, format="JPEG")
        expired = client.put(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot",
            headers={**receiver, "X-FlexDisplay-Command-ID": command_id},
            content=jpeg.getvalue(),
        )
        assert expired.status_code == 409


def test_camera_snapshot_rechecks_privacy_state_before_atomic_consume(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    receiver = {
        "X-FlexDisplay-Receiver-Token": "phone-receiver-secret",
        "Content-Type": "application/octet-stream",
    }
    jpeg = io.BytesIO()
    Image.new("RGB", (64, 64), "navy").save(jpeg, format="JPEG")
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers=_android_phone_headers())
        assert client.post(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot/request",
            headers=management,
        ).status_code == 200
        command = client.get("/api/v1/screen", headers=_android_phone_headers())
        command_id = command.headers["x-flexdisplay-command-id"]
        store = client.app.state.store
        original_consume = store.consume_camera_snapshot_command

        def tighten_then_consume(device_id: str, supplied_id: str) -> bool:
            store.update_metadata(device_id, {"camera_policy": "off"})
            return original_consume(device_id, supplied_id)

        monkeypatch.setattr(
            store, "consume_camera_snapshot_command", tighten_then_consume
        )
        rejected = client.put(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot",
            headers={**receiver, "X-FlexDisplay-Command-ID": command_id},
            content=jpeg.getvalue(),
        )
        assert rejected.status_code == 409
        record = store.get("ANDROID-PHONE01")
        assert record["dispatched_commands"] == ["camera-snapshot"]
        assert "camera_snapshot_at" not in record
        assert client.get(
            "/api/v1/devices/ANDROID-PHONE01/camera/snapshot",
            headers=management,
        ).status_code == 404


def test_studio_has_echo_spot_preview(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        studio = client.get("/studio/")
        assert studio.status_code == 200
        assert 'data-model="ROOK"' in studio.text
        assert 'data-model="CHECKERS"' in studio.text

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

        checkers_preview = client.post(
            "/api/v1/studio/preview",
            json={
                "model": "CHECKERS",
                "profile": {
                    "name": "show5-preview",
                    "pages": [
                        {
                            "title": "HOUSE",
                            "entities": [
                                {
                                    "entity_id": "static.state",
                                    "label": "Garage",
                                    "source": "static",
                                    "value": "Closed",
                                }
                            ],
                        }
                    ],
                },
            },
        )
        assert checkers_preview.status_code == 200
        with Image.open(io.BytesIO(checkers_preview.content)) as image:
            assert image.size == (960, 480)


def test_config_defaults_checkers_dimensions(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
devices:
  CHECKERS-SHOW501:
    name: Kitchen Show 5
    model: CHECKERS
""",
        encoding="utf-8",
    )

    from flexdisplay_bridge.config import load_config

    settings = load_config(config_path)
    device = settings.devices["CHECKERS-SHOW501"]
    assert device.width == 960
    assert device.height == 480


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


def test_checkers_mqtt_discovery_removes_embedded_firmware_and_sd_entities() -> None:
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
        "device_id": "CHECKERS-MQTT01",
        "model": "CHECKERS",
        "firmware": "android-0.1.0",
        "available_profiles": ["home"],
        "available_modes": ["home_assistant"],
    }

    service.publish_device(
        "CHECKERS-MQTT01",
        DeviceConfig(
            name="Kitchen Show 5",
            # Persisted profiles may lag a corrected receiver check-in. The
            # observed model must win so X-series controls cannot reappear.
            model="XTEINK_X3",
            width=960,
            height=480,
        ),
        state,
    )

    retained = {
        topic: payload
        for topic, payload, retain in client.messages
        if retain
    }
    assert retained["homeassistant/update/checkers_mqtt01/firmware/config"] == ""
    assert retained["homeassistant/sensor/checkers_mqtt01/sd_failure_events/config"] == ""
    assert retained[
        "homeassistant/binary_sensor/checkers_mqtt01/repeated_sd_failure/config"
    ] == ""
    assert retained["homeassistant/binary_sensor/checkers_mqtt01/sd_ready/config"] == ""
    assert retained["homeassistant/sensor/checkers_mqtt01/battery/config"] == ""
    assert retained[
        "homeassistant/binary_sensor/checkers_mqtt01/firmware_update_problem/config"
    ] == ""
    assert retained["homeassistant/button/checkers_mqtt01/firmware_retry/config"] == ""
    assert retained["homeassistant/button/checkers_mqtt01/rollout_reset/config"] == ""


def test_generic_mqtt_discovery_clears_unsupported_command_controls() -> None:
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
    service.publish_device(
        "ESP-MQTT01",
        DeviceConfig(name="Generic LCD", model="XTEINK_X3"),
        {
            "device_id": "ESP-MQTT01",
            "model": "ESP32-S3-LCD",
            "firmware": "1.0.0",
            "available_modes": ["home_assistant"],
        },
    )

    retained = {
        topic: payload
        for topic, payload, retain in client.messages
        if retain
    }
    assert retained["homeassistant/button/esp_mqtt01/restart/config"] == ""
    assert retained["homeassistant/button/esp_mqtt01/full_refresh/config"] == ""
    assert retained["homeassistant/button/esp_mqtt01/refresh/config"] != ""
    assert retained["homeassistant/update/esp_mqtt01/firmware/config"] == ""


def test_mqtt_prefers_the_authoritative_decorated_capability_contract() -> None:
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
    unknown_contract = resolve_device_capabilities("").to_dict()
    service.publish_device(
        "LEGACY-NOMODEL",
        DeviceConfig(name="Legacy", model="XTEINK_X3"),
        {
            "device_id": "LEGACY-NOMODEL",
            "model": "XTEINK_X3",
            "model_reported": False,
            "device_capabilities": unknown_contract,
        },
    )

    retained = {
        topic: payload
        for topic, payload, retain in client.messages
        if retain
    }
    assert retained["homeassistant/update/legacy_nomodel/firmware/config"] == ""
    assert retained["homeassistant/button/legacy_nomodel/restart/config"] == ""


def test_mqtt_screen_refresh_event_is_non_retained() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.messages: list[tuple[str, object, bool]] = []

        def publish(self, topic: str, payload: object, retain: bool = False) -> None:
            self.messages.append((topic, payload, retain))

    service = MqttService(
        MqttConfig(enabled=True),
        lambda device_id, command, payload: None,
    )
    client = FakeClient()
    service.client = client
    service.connected = True

    assert service.publish_screen_refresh(
        "LCD-KITCHEN",
        reason="command:refresh",
        command_id="LCD-KITCHEN-00000001",
        queued_at="2026-08-11T12:00:00+00:00",
    )
    topic, raw_payload, retained = client.messages[-1]
    payload = json.loads(str(raw_payload))
    assert topic == "flexdisplay/LCD-KITCHEN/event/screen"
    assert retained is False
    assert payload == {
        "event": "screen_refresh",
        "device_id": "LCD-KITCHEN",
        "reason": "command:refresh",
        "command_id": "LCD-KITCHEN-00000001",
        "queued_at": "2026-08-11T12:00:00+00:00",
    }


def test_mqtt_capable_color_display_gets_always_on_overlay_and_wake_event(
    tmp_path: Path,
) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.messages: list[tuple[str, object, bool]] = []

        def publish(self, topic: str, payload: object, retain: bool = False) -> None:
            self.messages.append((topic, payload, retain))

        def disconnect(self) -> None:
            pass

        def loop_stop(self) -> None:
            pass

    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-secret",
        mqtt=MqttConfig(enabled=False),
    )
    app = create_app(config)
    with TestClient(app) as client:
        screen = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "LCD-KITCHEN",
                "X-FlexDisplay-Model": "ESP32-S3-LCD",
                "X-FlexDisplay-Capabilities": (
                    "color,lcd,always-on-color,mqtt-screen-refresh"
                ),
            },
        )
        assert screen.headers["x-flexdisplay-refresh-interval"] == "60"
        assert screen.headers["x-flexdisplay-sleep-action"] == "awake"
        device = client.get(
            "/api/v1/devices/LCD-KITCHEN",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
        ).json()
        assert device["power_class"] == "always_on_color"
        assert device["display_technology"] == "lcd"
        assert device["refresh_delivery"] == "mqtt"

        fake = FakeClient()
        app.state.mqtt.client = fake
        app.state.mqtt.connected = True
        queued = client.post(
            "/api/v1/devices/LCD-KITCHEN/commands/refresh",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-secret"},
        )
        assert queued.status_code == 200
        assert any(
            topic == "flexdisplay/LCD-KITCHEN/event/screen"
            and json.loads(str(payload))["reason"] == "command:refresh"
            and not retain
            for topic, payload, retain in fake.messages
        )


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

        response_path = (
            f"/api/v1/devices/ROOK-ALERT01/notifications/{notification['id']}/response"
        )
        action = client.post(
            response_path,
            headers=paired,
            json={"outcome": "action", "action_id": "action-1", "confirmed": False},
        )
        assert action.status_code == 200
        assert action.json()["response"]["action_execution_success"] is True
        assert calls[-1] == ("light.turn_on", "light.porch")
        replay = client.post(
            response_path,
            headers=paired,
            json={"outcome": "action", "action_id": "action-1", "confirmed": False},
        )
        assert replay.status_code == 200
        assert replay.json()["duplicate"] is True
        assert calls == [("light.turn_on", "light.porch")]

        second = client.post(
            "/api/v1/devices/ROOK-ALERT01/notifications",
            headers=management,
            json={
                "title": "Garage",
                "actions": [
                    {
                        "label": "Open garage",
                        "service": "cover.open_cover",
                        "entity_id": "cover.garage_door",
                    }
                ],
            },
        ).json()["notification"]
        garage_path = (
            f"/api/v1/devices/ROOK-ALERT01/notifications/{second['id']}/response"
        )
        blocked = client.post(
            garage_path,
            headers=paired,
            json={"outcome": "action", "action_id": "action-1", "confirmed": False},
        )
        assert blocked.status_code == 409
        garage = client.post(
            garage_path,
            headers=paired,
            json={"outcome": "action", "action_id": "action-1", "confirmed": True},
        )
        assert garage.status_code == 200
        assert calls[-1] == ("cover.open_cover", "cover.garage_door")

        third = client.post(
            "/api/v1/devices/ROOK-ALERT01/notifications",
            headers=management,
            json={"title": "Dismiss me"},
        ).json()["notification"]
        dismissed = client.post(
            f"/api/v1/devices/ROOK-ALERT01/notifications/{third['id']}/response",
            headers=paired,
            json={"outcome": "dismissed", "confirmed": False},
        )
        assert dismissed.status_code == 200
        assert dismissed.json()["response"]["outcome"] == "dismissed"


def test_notification_response_auth_bounds_strict_types_and_one_shot(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    receiver_headers = {
        "X-FlexDisplay-ID": "ROOK-SECURE01",
        "X-FlexDisplay-Model": "ROOK",
        "X-FlexDisplay-Receiver-Token": "secure-token",
        "X-FlexDisplay-Capabilities": "android,color,touch,notifications",
    }
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    paired = {"X-FlexDisplay-Receiver-Token": "secure-token"}
    calls: list[str] = []
    monkeypatch.setattr(
        HomeAssistantClient,
        "call_service",
        lambda self, service, entity_id="", data=None: (
            calls.append(service) is None,
            "ok",
        ),
    )
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers=receiver_headers)
        notification = client.post(
            "/api/v1/devices/ROOK-SECURE01/notifications",
            headers=management,
            json={
                "title": "Secure",
                "duration": 300,
                "actions": [
                    {
                        "label": "Light",
                        "service": "light.turn_on",
                        "entity_id": "light.porch",
                    }
                ],
            },
        ).json()["notification"]
        path = (
            f"/api/v1/devices/ROOK-SECURE01/notifications/{notification['id']}/response"
        )
        before = client.app.state.store.get("ROOK-SECURE01")
        broker_before = client.app.state.rook.notification_contract(
            "ROOK-SECURE01", notification["id"]
        )
        assert client.post(
            path, json={"outcome": "dismissed", "confirmed": False}
        ).status_code == 401
        assert client.post(
            path,
            headers={"X-FlexDisplay-Receiver-Token": "wrong"},
            json={"outcome": "dismissed", "confirmed": False},
        ).status_code == 401
        assert client.app.state.store.get("ROOK-SECURE01") == before
        assert (
            client.app.state.rook.notification_contract(
                "ROOK-SECURE01", notification["id"]
            )
            == broker_before
        )

        for invalid_confirmed in ("false", 1, []):
            assert client.post(
                path,
                headers=paired,
                json={
                    "outcome": "action",
                    "action_id": "action-1",
                    "confirmed": invalid_confirmed,
                },
            ).status_code == 400
        oversized = b'{"outcome":"dismissed","padding":"' + b"x" * 2048 + b'"}'
        assert client.post(
            path,
            headers={**paired, "Content-Type": "application/json"},
            content=oversized,
        ).status_code == 413
        assert client.post(
            path,
            headers=paired,
            json={"outcome": "expired", "confirmed": False},
        ).status_code == 409

        accepted = client.post(
            path,
            headers=paired,
            json={
                "outcome": "action",
                "action_id": "action-1",
                "confirmed": False,
            },
        )
        assert accepted.status_code == 200
        public = accepted.json()["response"]
        assert public["trust"] == "paired_receiver"
        assert public["action_execution_success"] is True
        assert "action_label" not in public
        assert calls == ["light.turn_on"]
        replay = client.post(
            path,
            headers=paired,
            json={
                "outcome": "action",
                "action_id": "action-1",
                "confirmed": False,
            },
        )
        assert replay.json()["duplicate"] is True
        assert calls == ["light.turn_on"]


def test_notification_action_failure_is_terminal_and_not_rearmed(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    receiver_headers = {
        "X-FlexDisplay-ID": "ROOK-FAIL01",
        "X-FlexDisplay-Model": "ROOK",
        "X-FlexDisplay-Receiver-Token": "fail-token",
    }
    attempts: list[str] = []
    monkeypatch.setattr(
        HomeAssistantClient,
        "call_service",
        lambda self, service, entity_id="", data=None: (
            False if not attempts.append(service) else False,
            "unavailable",
        ),
    )
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers=receiver_headers)
        notification = client.post(
            "/api/v1/devices/ROOK-FAIL01/notifications",
            headers=management,
            json={
                "title": "Fail",
                "actions": [
                    {
                        "label": "Light",
                        "service": "light.turn_on",
                        "entity_id": "light.porch",
                    }
                ],
            },
        ).json()["notification"]
        path = f"/api/v1/devices/ROOK-FAIL01/notifications/{notification['id']}/response"
        payload = {
            "outcome": "action",
            "action_id": "action-1",
            "confirmed": False,
        }
        first = client.post(
            path,
            headers={"X-FlexDisplay-Receiver-Token": "fail-token"},
            json=payload,
        )
        assert first.status_code == 200
        assert first.json()["response"]["action_execution_success"] is False
        second = client.post(
            path,
            headers={"X-FlexDisplay-Receiver-Token": "fail-token"},
            json=payload,
        )
        assert second.json()["duplicate"] is True
        assert attempts == ["light.turn_on"]


def test_notification_replacement_validates_before_superseding_and_stays_consistent(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(_config(tmp_path), api_key="bridge-secret")
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    receiver_headers = {
        "X-FlexDisplay-ID": "ROOK-REPLACE1",
        "X-FlexDisplay-Model": "ROOK",
        "X-FlexDisplay-Receiver-Token": "replace-token",
    }
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers=receiver_headers)
        first = client.post(
            "/api/v1/devices/ROOK-REPLACE1/notifications",
            headers=management,
            json={"title": "First"},
        ).json()["notification"]
        for invalid in (
            {"title": "Bad chime", "chime": "arbitrary"},
            {
                "title": "Bad action",
                "actions": [
                    {
                        "label": "Shell",
                        "service": "shell_command.run",
                        "entity_id": "sensor.nope",
                    }
                ],
            },
        ):
            assert client.post(
                "/api/v1/devices/ROOK-REPLACE1/notifications",
                headers=management,
                json=invalid,
            ).status_code == 400
            assert (
                client.app.state.store.get("ROOK-REPLACE1")[
                    "active_notification_id"
                ]
                == first["id"]
            )
        monkeypatch.setattr(
            HomeAssistantClient,
            "camera_image",
            lambda self, entity_id: (_ for _ in ()).throw(ValueError("camera down")),
        )
        assert client.post(
            "/api/v1/devices/ROOK-REPLACE1/notifications",
            headers=management,
            json={"title": "Bad camera", "camera_entity": "camera.front"},
        ).status_code == 422
        assert (
            client.app.state.store.get("ROOK-REPLACE1")["active_notification_id"]
            == first["id"]
        )

        barrier = threading.Barrier(2)

        def create_named(title: str):
            barrier.wait()
            return client.post(
                "/api/v1/devices/ROOK-REPLACE1/notifications",
                headers=management,
                json={"title": title},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(create_named, ("Second", "Third")))
        assert all(response.status_code == 200 for response in responses)
        ids = {response.json()["notification"]["id"] for response in responses}
        record = client.app.state.store.get("ROOK-REPLACE1")
        active_id = record["active_notification_id"]
        assert active_id in ids
        assert client.app.state.rook.notification_contract(
            "ROOK-REPLACE1", active_id
        ) is not None
        outcomes = {
            item["notification_id"]: item["outcome"]
            for item in record["notification_response_history"]
        }
        assert outcomes[first["id"]] == "superseded"
        assert outcomes[(ids - {active_id}).pop()] == "superseded"


def test_notification_restart_and_elapsed_outcomes_are_distinct(tmp_path: Path) -> None:
    management = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}
    receiver_headers = {
        "X-FlexDisplay-ID": "ROOK-RESTART1",
        "X-FlexDisplay-Model": "ROOK",
        "X-FlexDisplay-Receiver-Token": "restart-token",
    }
    config = replace(_config(tmp_path), api_key="bridge-secret")
    with TestClient(create_app(config)) as client:
        client.get("/api/v1/screen", headers=receiver_headers)
        active = client.post(
            "/api/v1/devices/ROOK-RESTART1/notifications",
            headers=management,
            json={"title": "Restart", "duration": 300},
        ).json()["notification"]
    restarted = create_app(config)
    response = restarted.state.store.get("ROOK-RESTART1")[
        "last_notification_response"
    ]
    assert response["notification_id"] == active["id"]
    assert response["outcome"] == "bridge_restarted"

    expired_path = tmp_path / "expired-state.json"
    expired_config = replace(config, state_path=expired_path)
    with TestClient(create_app(expired_config)) as client:
        client.get("/api/v1/screen", headers=receiver_headers)
        expired = client.post(
            "/api/v1/devices/ROOK-RESTART1/notifications",
            headers=management,
            json={"title": "Expired", "duration": 300},
        ).json()["notification"]
    payload = json.loads(expired_path.read_text(encoding="utf-8"))
    payload["devices"]["ROOK-RESTART1"]["active_notification_expires_at"] = (
        "2000-01-01T00:00:00+00:00"
    )
    expired_path.write_text(json.dumps(payload), encoding="utf-8")
    expired_app = create_app(expired_config)
    response = expired_app.state.store.get("ROOK-RESTART1")[
        "last_notification_response"
    ]
    assert response["notification_id"] == expired["id"]
    assert response["outcome"] == "server_expired"


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
