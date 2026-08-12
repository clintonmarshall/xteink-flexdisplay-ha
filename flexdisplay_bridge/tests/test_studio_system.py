from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flexdisplay_bridge.app import _safe_host_port, create_app
from flexdisplay_bridge.config import (
    BridgeConfig,
    FirmwareConfig,
    FlexHubConfig,
    HomeAssistantConfig,
    MqttConfig,
    ProvisioningConfig,
)
from flexdisplay_bridge.mqtt_service import MqttService


def _firmware(version: str, marker: str) -> FirmwareConfig:
    return FirmwareConfig(
        version=version,
        url="https://firmware.example.test/release.bin",
        sha256=marker * 64,
        size=5_500_000,
        canary_required=False,
        require_usb_for_canary=False,
        mirror_enabled=False,
    )


def _check_in(client: TestClient, device_id: str, model: str) -> None:
    response = client.get(
        "/api/v1/screen",
        headers={
            "X-FlexDisplay-ID": device_id,
            "X-FlexDisplay-Model": model,
            "X-FlexDisplay-Firmware": "1.0.0",
            "X-FlexDisplay-SD-Ready": "true",
            "X-FlexDisplay-USB-Connected": "true",
            "X-FlexDisplay-Battery-Percent": "100",
        },
    )
    assert response.status_code == 200


def test_device_list_and_detail_expose_the_same_capability_contract(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        firmware=_firmware("1.5.0-flexdisplay.9.0.0", "a"),
        note4_firmware=_firmware("1.2.9-note4", "b"),
    )
    models = {
        "X3-CAPS01": "XTEINK_X3",
        "X4-CAPS02": "XTEINK_X4",
        "N4-CAPS03": "ZECTRIX_NOTE4",
        "ROOK-CAPS04": "ROOK",
        "SHOW-CAPS05": "CHECKERS",
        "ESP-CAPS06": "ESP32-S3-LCD",
    }

    with TestClient(create_app(config)) as client:
        for device_id, model in models.items():
            _check_in(client, device_id, model)

        compact = {
            record["device_id"]: record
            for record in client.get("/api/v1/devices?compact=true").json()["devices"]
        }
        for device_id in models:
            detail = client.get(f"/api/v1/devices/{device_id}").json()
            assert compact[device_id]["device_capabilities"] == detail[
                "device_capabilities"
            ]

        assert compact["X3-CAPS01"]["firmware_provider"] == "xteink"
        assert compact["X4-CAPS02"]["device_capabilities"]["firmware"][
            "supports_xteink_ota"
        ] is True
        assert compact["N4-CAPS03"]["firmware_provider"] == "note4"
        assert compact["ROOK-CAPS04"]["firmware_provider"] == "android_app"
        assert compact["SHOW-CAPS05"]["firmware_provider"] == "android_app"
        assert compact["ESP-CAPS06"]["firmware_provider"] == "none"
        assert compact["ESP-CAPS06"]["latest_firmware"] == "1.0.0"
        assert compact["ESP-CAPS06"]["update_available"] is False


def test_missing_model_header_never_grants_xteink_firmware_to_unknown_identity(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        firmware=_firmware("1.5.0-flexdisplay.9.0.0", "9"),
    )

    with TestClient(create_app(config)) as client:
        checked_in = client.get(
            "/api/v1/screen",
            headers={"X-FlexDisplay-ID": "ESP-NOMODE01"},
        )
        assert checked_in.status_code == 200
        assert "x-flexdisplay-latest-firmware" not in checked_in.headers

        device = client.get("/api/v1/devices/ESP-NOMODE01").json()
        assert device["model"] == "UNKNOWN"
        assert device["firmware_provider"] == "none"
        assert device["update_available"] is False

        install = client.post(
            "/api/v1/devices/ESP-NOMODE01/commands/install"
        )
        assert install.status_code == 409
        assert "not managed" in install.json()["detail"]


def test_auto_provisioning_filters_defaults_and_reconciles_corrected_identity(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        provisioning=ProvisioningConfig(default_mode="reader"),
    )

    with TestClient(create_app(config)) as client:
        first = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-RECLASS01",
                "X-FlexDisplay-Model": "XTEINK_X3",
            },
        )
        assert first.headers["x-flexdisplay-assigned-mode"] == "reader"

        corrected = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-RECLASS01",
                "X-FlexDisplay-Model": "ROOK",
            },
        )
        record = client.app.state.store.get("X3-RECLASS01")

        unknown = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "UNKNOWN-PROVISION02",
                "X-FlexDisplay-Model": "ACME PANEL",
            },
        )
        unknown_record = client.app.state.store.get("UNKNOWN-PROVISION02")
        unknown_device = client.get(
            "/api/v1/devices/UNKNOWN-PROVISION02"
        ).json()

    assert corrected.status_code == 200
    assert corrected.headers["x-flexdisplay-assigned-mode"] == "home_assistant"
    assert record["assigned_mode"] == "home_assistant"
    assert "assigned_low_battery_percent" not in record
    assert "assigned_intelligent_sleep" not in record
    assert "assigned_open_display_transport_policy" not in record
    assert record["provisioning_capability_reconciled_reason"] == (
        "capability-reconciled"
    )
    assert "x-flexdisplay-provisioned" not in unknown.headers
    assert "x-flexdisplay-assigned-mode" not in unknown.headers
    assert unknown_record.get("assigned_mode") is None
    assert unknown_record.get("provisioned") is not True
    assert "assigned_mode" not in unknown_device
    assert "assigned_intelligent_sleep" not in unknown_device
    assert unknown_device["available_modes"] == []
    assert unknown_device["sleep_action"] == "awake"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("2001:db8::1", "[2001:db8::1]:1883"),
        ("user:secret@mqtt.example:1884", "mqtt.example:1883"),
        ("user:pw@[2001:db8::1]", "[2001:db8::1]:1883"),
    ],
)
def test_safe_host_port_redacts_userinfo_and_formats_ipv6(
    host: str, expected: str
) -> None:
    rendered = _safe_host_port(host, 1883)

    assert rendered == expected
    assert "user" not in rendered
    assert "secret" not in rendered
    assert "pw" not in rendered


def test_check_in_cancels_legacy_install_for_ineligible_device(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        firmware=_firmware("1.5.0-flexdisplay.9.0.0", "8"),
    )

    with TestClient(create_app(config)) as client:
        _check_in(client, "ESP-LEGACY01", "ESP32-S3-LCD")
        client.app.state.store.queue_command("ESP-LEGACY01", "install")

        checked_in = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "ESP-LEGACY01",
                "X-FlexDisplay-Model": "ESP32-S3-LCD",
            },
        )

        assert checked_in.status_code == 200
        assert "install" not in checked_in.headers.get("x-flexdisplay-commands", "")
        assert "x-flexdisplay-firmware-url" not in checked_in.headers
        record = client.app.state.store.get("ESP-LEGACY01")
        assert record["pending_commands"] == []
        assert record.get("dispatched_commands") == []
        assert record["firmware_update_error"] == (
            "install:firmware-channel-mismatch"
        )


def test_check_in_cancels_install_when_device_changes_firmware_provider(
    tmp_path: Path,
) -> None:
    x_firmware = _firmware("1.5.0-flexdisplay.9.0.0", "8")
    note4_firmware = _firmware("1.2.9-note4", "7")
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        firmware=x_firmware,
        note4_firmware=note4_firmware,
    )

    with TestClient(create_app(config)) as client:
        _check_in(client, "X3-CHANGED01", "XTEINK_X3")
        queued = client.post("/api/v1/devices/X3-CHANGED01/commands/install")
        assert queued.status_code == 200
        assert queued.json()["device"]["firmware_update_provider"] == "xteink"

        changed = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-CHANGED01",
                "X-FlexDisplay-Model": "ZECTRIX_NOTE4",
                "X-FlexDisplay-Firmware": "1.0.0",
                "X-FlexDisplay-USB-Connected": "true",
                "X-FlexDisplay-Battery-Percent": "100",
            },
        )

        assert changed.status_code == 200
        assert "install" not in changed.headers.get("x-flexdisplay-commands", "")
        assert "x-flexdisplay-firmware-url" not in changed.headers
        record = client.app.state.store.get("X3-CHANGED01")
        assert record["firmware_update_error"] == (
            "install:firmware-channel-mismatch"
        )


def test_check_in_cancels_note4_install_when_identity_changes_to_x_series(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        firmware=_firmware("1.5.0-flexdisplay.9.0.0", "8"),
        note4_firmware=_firmware("1.2.9-note4", "7"),
    )

    with TestClient(create_app(config)) as client:
        _check_in(client, "N4-CHANGED02", "ZECTRIX_NOTE4")
        queued = client.post("/api/v1/devices/N4-CHANGED02/commands/install")
        assert queued.status_code == 200
        assert queued.json()["device"]["firmware_update_provider"] == "note4"

        changed = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "N4-CHANGED02",
                "X-FlexDisplay-Model": "XTEINK_X4",
                "X-FlexDisplay-Firmware": "1.0.0",
                "X-FlexDisplay-SD-Ready": "true",
                "X-FlexDisplay-USB-Connected": "true",
                "X-FlexDisplay-Battery-Percent": "100",
            },
        )

        assert changed.status_code == 200
        assert "install" not in changed.headers.get("x-flexdisplay-commands", "")
        assert "x-flexdisplay-firmware-url" not in changed.headers
        record = client.app.state.store.get("N4-CHANGED02")
        assert record["firmware_update_error"] == (
            "install:firmware-channel-mismatch"
        )


def test_mixed_fleet_firmware_scope_excludes_incompatible_devices_before_plan(
    tmp_path: Path,
) -> None:
    firmware = _firmware("1.5.0-flexdisplay.9.0.0", "c")
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-secret",
        firmware=firmware,
        note4_firmware=_firmware("1.2.9-note4", "d"),
    )
    models = {
        "X3-SAFE01": "XTEINK_X3",
        "X4-SAFE02": "XTEINK_X4",
        "N4-SAFE03": "ZECTRIX_NOTE4",
        "ROOK-SAFE04": "ROOK",
        "SHOW-SAFE05": "CHECKERS",
        "ESP-SAFE06": "ESP32-S3-LCD",
    }
    auth = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}

    with TestClient(create_app(config)) as client:
        for device_id, model in models.items():
            _check_in(client, device_id, model)

        response = client.post(
            "/api/v1/fleet/firmware/install",
            headers=auth,
            json={"scope": "all", "confirm_version": firmware.version},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["targets"] == ["X3-SAFE01", "X4-SAFE02"]
        assert set(payload["blocked"]).isdisjoint(payload["excluded"])
        assert set(payload["excluded"]) == {
            "N4-SAFE03",
            "ROOK-SAFE04",
            "SHOW-SAFE05",
            "ESP-SAFE06",
        }
        assert client.app.state.store.firmware_rollout()["planned_devices"] == [
            "X3-SAFE01",
            "X4-SAFE02",
        ]
        for device_id in payload["excluded"]:
            record = client.app.state.store.get(device_id)
            assert "install" not in (record.get("pending_commands") or [])
            assert "install" not in (record.get("dispatched_commands") or [])


def test_all_ineligible_fleet_scope_returns_conflict_without_persisting_plan(
    tmp_path: Path,
) -> None:
    firmware = _firmware("1.5.0-flexdisplay.9.0.0", "e")
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-secret",
        firmware=firmware,
        note4_firmware=_firmware("1.2.9-note4", "f"),
    )
    auth = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}

    with TestClient(create_app(config)) as client:
        for device_id, model in {
            "N4-BLOCK01": "ZECTRIX_NOTE4",
            "ROOK-BLOCK02": "ROOK",
            "ESP-BLOCK03": "ESP32-S3-LCD",
        }.items():
            _check_in(client, device_id, model)

        response = client.post(
            "/api/v1/fleet/firmware/install",
            headers=auth,
            json={"scope": "all", "confirm_version": firmware.version},
        )

        assert response.status_code == 409
        assert "eligible" in response.json()["detail"]
        assert client.app.state.store.firmware_rollout() == {}


def test_policy_and_commands_are_filtered_by_management_capabilities(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-secret",
    )
    auth = {"X-FlexDisplay-Bridge-Key": "bridge-secret"}

    with TestClient(create_app(config)) as client:
        _check_in(client, "X3-POLICY01", "XTEINK_X3")
        _check_in(client, "MYSTERY-POLICY02", "Acme Mystery Panel")
        _check_in(client, "ESP-COMMAND03", "ESP32-S3-LCD")

        applied = client.put(
            "/api/v1/fleet/policy",
            headers=auth,
            json={"profile": "balanced", "scope": "all"},
        )
        assert applied.status_code == 200
        assert applied.json()["targets"] == ["X3-POLICY01", "ESP-COMMAND03"]
        assert applied.json()["excluded"] == {
            "MYSTERY-POLICY02": (
                "Device does not advertise a compatible fleet policy contract"
            )
        }

        rejected = client.post(
            "/api/v1/devices/ESP-COMMAND03/commands/restart",
            headers=auth,
        )
        assert rejected.status_code == 409
        assert "not supported" in rejected.json()["detail"]
        assert client.app.state.store.get("ESP-COMMAND03")["pending_commands"] == []

        accepted = client.post(
            "/api/v1/devices/ESP-COMMAND03/commands/refresh",
            headers=auth,
        )
        assert accepted.status_code == 200

        per_mode = client.put(
            "/api/v1/fleet/policy",
            headers=auth,
            json={"profile": "balanced", "scope": "all", "mode": "reader"},
        )
        assert per_mode.status_code == 200
        assert per_mode.json()["targets"] == ["X3-POLICY01"]
        assert "ESP-COMMAND03" in per_mode.json()["excluded"]
        generic = client.get("/api/v1/devices/ESP-COMMAND03").json()
        assert generic.get("assigned_mode") != "reader"
        assert {
            "low_battery_multiplier",
            "open_display_transport_policy",
        }.issubset(set(applied.json()["filtered_fields"]["ESP-COMMAND03"]))

        unsupported_provisioning = client.put(
            "/api/v1/devices/ESP-COMMAND03/provision",
            headers=auth,
            json={"low_battery_percent": 25},
        )
        assert unsupported_provisioning.status_code == 409
        assert "low_battery_percent" in unsupported_provisioning.json()["detail"]


def test_global_x_rollout_reset_is_never_exposed_on_other_device_families(
    tmp_path: Path,
) -> None:
    firmware = _firmware("1.5.0-flexdisplay.9.0.0", "6")
    config = BridgeConfig(state_path=tmp_path / "state.json", firmware=firmware)

    with TestClient(create_app(config)) as client:
        for device_id, model in {
            "X4-RESET01": "XTEINK_X4",
            "N4-RESET02": "ZECTRIX_NOTE4",
            "ROOK-RESET03": "ROOK",
            "ESP-RESET04": "ESP32-S3-LCD",
        }.items():
            _check_in(client, device_id, model)
        store = client.app.state.store
        with store._lock:
            store._state["firmware_rollout"] = {
                "target_version": firmware.version,
                "status": "failed",
            }

        devices = {
            record["device_id"]: record
            for record in client.get("/api/v1/devices").json()["devices"]
        }

    assert devices["X4-RESET01"]["firmware_rollout_reset_ready"] is True
    for device_id in ("N4-RESET02", "ROOK-RESET03", "ESP-RESET04"):
        assert devices[device_id]["firmware_rollout_reset_ready"] is False


def test_mqtt_commands_use_the_same_capability_and_firmware_provider_gates(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        firmware=_firmware("1.5.0-flexdisplay.9.0.0", "4"),
        note4_firmware=_firmware("1.2.9-note4", "3"),
        mqtt=MqttConfig(enabled=False),
    )

    with TestClient(create_app(config)) as client:
        _check_in(client, "ESP-MQTT01", "ESP32-S3-LCD")
        _check_in(client, "N4-MQTT02", "ZECTRIX_NOTE4")

        client.app.state.mqtt.on_command("ESP-MQTT01", "restart", "PRESS")
        generic = client.app.state.store.get("ESP-MQTT01")
        assert generic["pending_commands"] == []
        assert generic["last_management_action"] == "restart"
        assert generic["last_management_action_success"] is False

        client.app.state.mqtt.on_command("N4-MQTT02", "install", "PRESS")
        note4 = client.app.state.store.get("N4-MQTT02")
        assert note4["pending_commands"] == ["install"]
        assert note4["firmware_update_provider"] == "note4"
        assert note4["firmware_update_target"] == "1.2.9-note4"
        assert client.app.state.store.firmware_rollout() == {}

        _check_in(client, "UNKNOWN-MQTT03", "ACME-MYSTERY")
        client.app.state.mqtt.on_command(
            "UNKNOWN-MQTT03", "resend-screen", "PRESS"
        )
        unknown = client.app.state.store.get("UNKNOWN-MQTT03")
        assert unknown["pending_commands"] == []
        assert unknown["last_management_action_success"] is False


def test_system_status_is_authenticated_and_server_side_redacted(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("FLEXDISPLAY_FIRMWARE_CONFIGURED_VERSION", "legacy-option")
    monkeypatch.setenv("FLEXDISPLAY_FIRMWARE_CONFIG_SOURCE", "packaged_release")
    sentinels = {
        "api_key": "system-api-secret",
        "ha_token": "system-ha-token",
        "mqtt_password": "system-mqtt-password",
        "flexhub_pin": "system-flexhub-pin",
        "url_user": "system-url-user",
        "url_password": "system-url-password",
        "url_query": "system-query-secret",
    }
    firmware = _firmware("1.5.0-flexdisplay.9.0.0", "1")
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key=sentinels["api_key"],
        home_assistant=HomeAssistantConfig(
            base_url=(
                f"http://{sentinels['url_user']}:{sentinels['url_password']}@"
                f"homeassistant.example.test?token={sentinels['url_query']}"
            ),
            token=sentinels["ha_token"],
        ),
        mqtt=MqttConfig(
            enabled=False,
            host=(
                f"{sentinels['url_user']}:{sentinels['url_password']}@"
                f"mqtt.example.test?token={sentinels['url_query']}"
            ),
            username="mqtt-user",
            password=sentinels["mqtt_password"],
        ),
        flexhub=FlexHubConfig(
            url="http://flexhub.example.test",
            access_pin=sentinels["flexhub_pin"],
        ),
        firmware=firmware,
    )

    with TestClient(create_app(config)) as client:
        assert client.get("/api/v1/system").status_code == 401
        response = client.get(
            "/api/v1/system",
            headers={"X-FlexDisplay-Bridge-Key": sentinels["api_key"]},
        )

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload)
    for sentinel in sentinels.values():
        assert sentinel not in serialized
    assert payload["effective_settings"]["x_series_firmware"]["value"] == (
        firmware.version
    )
    assert payload["effective_settings"]["x_series_firmware"][
        "configured_value"
    ] == "legacy-option"
    assert payload["effective_settings"]["x_series_firmware"][
        "apply_state"
    ] == "resolved_override"
    assert payload["connections"]["home_assistant"]["status"] == "configured"
    assert payload["connections"]["mqtt"]["endpoint"] == "mqtt.example.test:1883"
    assert payload["effective_settings"]["bridge_api_key"]["sensitive"] is True
    assert payload["effective_settings"]["mqtt_credentials"]["sensitive"] is True


def test_system_and_health_replace_secret_bearing_runtime_errors(
    tmp_path: Path,
) -> None:
    secret = "runtime-error-secret"
    config = BridgeConfig(state_path=tmp_path / "state.json", api_key="bridge-key")

    with TestClient(create_app(config)) as client:
        client.app.state.flexhub._error = (
            f"redirected to https://user:pw@example.test/?token={secret}"
        )
        client.app.state.firmware_mirror._status.update(
            {
                "state": "failed",
                "last_error": (
                    f"GET https://user:pw@example.test/fw.bin?token={secret} failed"
                ),
            }
        )
        system = client.get(
            "/api/v1/system",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-key"},
        ).json()
        health = client.get("/healthz").json()

    assert secret not in json.dumps(system)
    assert secret not in json.dumps(health)
    assert "user:pw" not in json.dumps(system)
    assert "user:pw" not in json.dumps(health)
    assert "redirected" in system["connections"]["flexhub"]["detail"].lower()
    assert "cached_path" not in health["firmware_mirror"]


def test_system_reports_saved_flexhub_override_and_safe_generic_ha_link(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-key",
        flexhub=FlexHubConfig(url="http://home-assistant-option.test"),
    )

    with TestClient(create_app(config)) as client:
        client.app.state.flexhub.configure("http://bridge-saved.test")
        payload = client.get(
            "/api/v1/system",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-key"},
        ).json()
        client.app.state.flexhub.configure("", "")
        disconnected = client.get(
            "/api/v1/system",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-key"},
        ).json()

    setting = payload["effective_settings"]["flexhub_endpoint"]
    assert setting["value"] == "http://bridge-saved.test"
    assert setting["configured_value"] == "http://home-assistant-option.test"
    assert setting["apply_state"] == "saved_override"
    assert setting["owner"] == "Bridge saved state"
    link = payload["links"]["home_assistant_app_settings"]["url"]
    assert link == "/config/apps"
    assert "629898c9" not in json.dumps(payload)
    disconnected_setting = disconnected["effective_settings"]["flexhub_endpoint"]
    assert disconnected_setting["value"] == ""
    assert disconnected_setting["configured_value"] == (
        "http://home-assistant-option.test"
    )
    assert disconnected_setting["apply_state"] == "saved_disconnect"


def test_system_reports_saved_flexhub_pin_override_with_same_url(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-key",
        flexhub=FlexHubConfig(
            url="http://same-endpoint.test",
            access_pin="home-assistant-pin",
        ),
    )

    with TestClient(create_app(config)) as client:
        client.app.state.flexhub.configure(
            "http://same-endpoint.test", "bridge-saved-pin"
        )
        payload = client.get(
            "/api/v1/system",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-key"},
        ).json()

    setting = payload["effective_settings"]["flexhub_endpoint"]
    assert setting["value"] == "http://same-endpoint.test"
    assert setting["configured_value"] == ""
    assert setting["apply_state"] == "effective"
    pin = payload["effective_settings"]["flexhub_access_pin"]
    assert pin["sensitive"] is True
    assert pin["apply_state"] == "saved_override"
    assert pin["owner"] == "Bridge saved state"
    assert "saved Bridge PIN" in pin["detail"]


def test_flexhub_summary_never_exposes_pin_equality_oracle(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        flexhub=FlexHubConfig(url="http://hub.test", access_pin="2468"),
    )

    with TestClient(create_app(config)) as client:
        equal = client.put(
            "/api/v1/flexhub/settings",
            json={"url": "http://hub.test", "access_pin": "2468"},
        ).json()
        different = client.put(
            "/api/v1/flexhub/settings",
            json={"url": "http://hub.test", "access_pin": "0000"},
        ).json()

    assert "saved_pin_override" not in equal
    assert "saved_pin_override" not in different
    assert equal["saved_pin_authoritative"] is True
    assert different["saved_pin_authoritative"] is True


def test_public_firmware_surfaces_redact_mirror_failures(
    tmp_path: Path, monkeypatch
) -> None:
    secret = "MIRROR-LEAK-SENTINEL"
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-key",
        firmware=_firmware("1.5.0-flexdisplay.9.0.0", "a"),
    )

    with TestClient(create_app(config)) as client:
        mirror = client.app.state.firmware_mirror
        mirror._status["last_error"] = (
            f"download failed https://user:pass@example.test/fw.bin?token={secret}"
        )
        mirror._status["cached_path"] = f"/private/{secret}/firmware.bin"
        policies = client.get("/api/v1/fleet/policies")

        def fail_prepare(*args, **kwargs):
            from flexdisplay_bridge.firmware_mirror import FirmwareMirrorError

            raise FirmwareMirrorError(
                f"connection to https://example.test/fw.bin?token={secret} failed"
            )

        monkeypatch.setattr(mirror, "prepare", fail_prepare)
        binary = client.get("/api/v1/firmware/current.bin")
        refresh = client.post(
            "/api/v1/firmware/mirror/refresh",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-key"},
        )

    assert policies.status_code == 200
    assert binary.status_code == 503
    assert refresh.status_code == 503
    combined = policies.text + binary.text + refresh.text
    assert secret not in combined
    assert "cached_path" not in policies.text
    assert policies.json()["firmware"]["mirror"]["last_error"] == (
        "The firmware mirror could not prepare the configured release."
    )


def test_identity_correction_cancels_stale_unsupported_commands(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json")

    with TestClient(create_app(config)) as client:
        _check_in(client, "X3-RECLASS-CMD", "XTEINK_X3")
        queued = client.post(
            "/api/v1/devices/X3-RECLASS-CMD/commands/power-off"
        )
        corrected = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-RECLASS-CMD",
                "X-FlexDisplay-Model": "ACME-MYSTERY",
            },
        )
        record = client.app.state.store.get("X3-RECLASS-CMD")

    assert queued.status_code == 200
    assert corrected.headers["x-flexdisplay-commands"] == ""
    assert record["pending_commands"] == []
    assert record.get("dispatched_commands") == []
    assert "power-off" in record["last_cancelled_commands"]


def test_missing_model_header_preserves_explicit_non_x_identity(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        firmware=_firmware("1.5.0-flexdisplay.9.0.0", "b"),
    )

    with TestClient(create_app(config)) as client:
        explicit = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-WAS-ROOK",
                "X-FlexDisplay-Model": "ROOK",
            },
        )
        missing = client.get(
            "/api/v1/screen",
            headers={"X-FlexDisplay-ID": "X3-WAS-ROOK"},
        )
        device = client.get("/api/v1/devices/X3-WAS-ROOK").json()

    assert explicit.status_code == 200
    assert missing.status_code == 200
    assert device["model"] == "ROOK"
    assert device["firmware_provider"] == "android_app"
    assert device["device_capabilities"]["firmware"]["supports_xteink_ota"] is False
    assert "x-flexdisplay-latest-firmware" not in missing.headers


def test_system_workspace_has_an_in_place_api_key_unlock(tmp_path: Path) -> None:
    with TestClient(create_app(BridgeConfig(state_path=tmp_path / "state.json"))) as client:
        html = client.get("/studio/").text

    assert 'id="systemAuthPanel"' in html
    assert 'id="systemApiKey"' in html
    assert 'id="systemUnlockButton"' in html
    assert 'entry.configured_value !== ""' in html


def test_system_ha_apps_link_escapes_the_direct_bridge_port(tmp_path: Path) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json", api_key="bridge-key")

    with TestClient(create_app(config), base_url="http://10.200.40.4:8099") as client:
        payload = client.get(
            "/api/v1/system",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-key"},
        ).json()

    assert payload["links"]["home_assistant_app_settings"]["url"] == (
        "http://10.200.40.4:8123/config/apps"
    )


def test_system_firmware_channels_distinguish_direct_and_invalid_delivery(
    tmp_path: Path,
) -> None:
    direct = FirmwareConfig(
        version="1.5.0-flexdisplay.9.0.0",
        url="https://firmware.example.test/release.bin",
        sha256="5" * 64,
        size=5_500_000,
        canary_required=False,
        require_usb_for_canary=False,
        mirror_enabled=False,
    )
    invalid_note4 = FirmwareConfig(
        version="1.2.9-note4",
        url="https://firmware.example.test/note4.bin",
        sha256="bad",
        size=5_500_000,
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-key",
        firmware=direct,
        note4_firmware=invalid_note4,
    )

    with TestClient(create_app(config)) as client:
        channels = client.get(
            "/api/v1/system",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-key"},
        ).json()["firmware_channels"]

    assert channels["x_series"]["status"] == "ready"
    assert "Direct firmware delivery" in channels["x_series"]["detail"]
    assert channels["note4"]["status"] == "not_configured"
    assert "SHA-256" in channels["note4"]["detail"]


def test_system_does_not_claim_mqtt_discovery_when_hacs_owns_entities(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(MqttService, "start", lambda self: None)
    monkeypatch.setattr(MqttService, "stop", lambda self: None)
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-key",
        mqtt=MqttConfig(enabled=True, entity_source="hacs"),
    )

    with TestClient(create_app(config)) as client:
        client.app.state.mqtt.connected = True
        connection = client.get(
            "/api/v1/system",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-key"},
        ).json()["connections"]["mqtt"]

    assert connection["status"] == "connected"
    assert connection["discovery_enabled"] is False
    assert "discovery is disabled" in connection["detail"].lower()


def test_system_formats_raw_ipv6_mqtt_host_without_truncation(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-key",
        mqtt=MqttConfig(enabled=False, host="2001:db8::1", port=1883),
    )

    with TestClient(create_app(config)) as client:
        endpoint = client.get(
            "/api/v1/system",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-key"},
        ).json()["connections"]["mqtt"]["endpoint"]

    assert endpoint == "[2001:db8::1]:1883"


def test_studio_exposes_bridge_and_connections_workspace(tmp_path: Path) -> None:
    with TestClient(create_app(BridgeConfig(state_path=tmp_path / "state.json"))) as client:
        html = client.get("/studio/").text

    assert "Bridge &amp; Connections" in html
    assert 'id="refreshSystemStatus"' in html
    assert 'id="systemSettingsBody"' in html
    assert 'id="systemFirmwareChannels"' in html
    assert 'id="systemDiagnosticLinks"' in html
    assert 'api("system")' in html


def test_system_status_does_not_describe_unconfigured_flexhub_as_operational(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(state_path=tmp_path / "state.json", api_key="bridge-key")

    with TestClient(create_app(config)) as client:
        response = client.get(
            "/api/v1/system",
            headers={"X-FlexDisplay-Bridge-Key": "bridge-key"},
        )

    assert response.status_code == 200
    flexhub = response.json()["connections"]["flexhub"]
    assert flexhub["status"] == "not_configured"
    assert flexhub["detail"] == "No FlexHub endpoint is configured."


def test_saved_groups_drive_dry_run_policy_and_firmware_previews(
    tmp_path: Path,
) -> None:
    firmware = _firmware("1.5.0-flexdisplay.9.0.0", "7")
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-key",
        firmware=firmware,
    )
    auth = {"X-FlexDisplay-Bridge-Key": "bridge-key"}

    with TestClient(create_app(config)) as client:
        _check_in(client, "X3-GROUP01", "XTEINK_X3")
        _check_in(client, "ESP-GROUP02", "ESP32-S3-LCD")
        saved = client.put(
            "/api/v1/fleet/groups/reception",
            headers=auth,
            json={
                "label": "Reception",
                "device_ids": ["X3-GROUP01", "ESP-GROUP02"],
            },
        )
        before = {
            device_id: client.app.state.store.get(device_id)
            for device_id in ("X3-GROUP01", "ESP-GROUP02")
        }
        policy = client.post(
            "/api/v1/fleet/policy/preview",
            headers=auth,
            json={
                "profile": "balanced",
                "scope": "group",
                "group_id": "reception",
            },
        )
        firmware_preview = client.post(
            "/api/v1/fleet/firmware/preview",
            headers=auth,
            json={"scope": "group", "group_id": "reception"},
        )
        after = {
            device_id: client.app.state.store.get(device_id)
            for device_id in before
        }
        listed = client.get("/api/v1/fleet/groups", headers=auth)

    assert saved.status_code == 200
    assert saved.json()["group"]["resolved_count"] == 2
    assert policy.status_code == 200
    assert policy.json()["target_count"] == 2
    assert firmware_preview.status_code == 200
    assert firmware_preview.json()["eligible"] == ["X3-GROUP01"]
    assert "ESP-GROUP02" in firmware_preview.json()["excluded"]
    assert before == after
    assert client.app.state.store.firmware_rollout() == {}
    assert listed.json()["groups"][0]["resolved_device_ids"] == [
        "X3-GROUP01",
        "ESP-GROUP02",
    ]


def test_device_identity_and_management_timeline_follow_model_correction(
    tmp_path: Path,
) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key="bridge-key",
    )
    auth = {"X-FlexDisplay-Bridge-Key": "bridge-key"}

    with TestClient(create_app(config)) as client:
        _check_in(client, "X3-IDENTITY01", "XTEINK_X3")
        _check_in(client, "X3-IDENTITY01", "ROOK")
        assert (
            client.get("/api/v1/devices/X3-IDENTITY01/timeline").status_code
            == 401
        )
        response = client.get(
            "/api/v1/devices/X3-IDENTITY01/timeline",
            headers=auth,
        )
        device = client.get("/api/v1/devices/X3-IDENTITY01").json()

    assert response.status_code == 200
    payload = response.json()
    assert payload["identity"]["source"] == "reported"
    assert payload["identity"]["conflict"] is True
    assert payload["identity"]["firmware_owner"] == "android_app"
    assert any(event["type"] == "identity" for event in payload["events"])
    assert device["identity"] == payload["identity"]


def test_support_bundle_is_authenticated_redacted_and_includes_drift_alerts(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("FLEXDISPLAY_FIRMWARE_CONFIGURED_VERSION", "old-release")
    monkeypatch.setenv("FLEXDISPLAY_FIRMWARE_CONFIG_SOURCE", "packaged_release")
    sentinels = {
        "api": "bundle-api-secret",
        "ha": "bundle-ha-token",
        "mqtt": "bundle-mqtt-password",
        "pin": "bundle-flexhub-pin",
    }
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key=sentinels["api"],
        home_assistant=HomeAssistantConfig(token=sentinels["ha"]),
        mqtt=MqttConfig(enabled=False, password=sentinels["mqtt"]),
        flexhub=FlexHubConfig(
            url="http://flexhub.example.test",
            access_pin=sentinels["pin"],
        ),
        firmware=_firmware("1.5.0-flexdisplay.9.0.0", "8"),
    )
    auth = {"X-FlexDisplay-Bridge-Key": sentinels["api"]}

    with TestClient(create_app(config)) as client:
        _check_in(client, "X4-BUNDLE01", "XTEINK_X4")
        assert client.get("/api/v1/system/support-bundle").status_code == 401
        response = client.get("/api/v1/system/support-bundle", headers=auth)

    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith(
        "flexdisplay-support-bundle.json"
    )
    serialized = response.text
    for sentinel in sentinels.values():
        assert sentinel not in serialized
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["fleet"]["device_count"] == 1
    assert payload["system"]["alerts"]
    assert any(
        alert["category"] == "configuration_drift"
        for alert in payload["system"]["alerts"]
    )
    assert all(
        "detail" not in event
        for event in payload["fleet"]["devices"][0]["timeline"]
    )


def test_studio_exposes_mixed_fleet_lifecycle_operations(tmp_path: Path) -> None:
    with TestClient(create_app(BridgeConfig(state_path=tmp_path / "state.json"))) as client:
        html = client.get("/studio/").text

    for element_id in (
        "downloadSupportBundle",
        "systemAlertList",
        "fleetGroupSelect",
        "saveFleetGroup",
        "previewFleetPolicy",
        "previewFleetFirmware",
        "fleetImpactPreview",
        "deviceTimelineDialog",
    ):
        assert f'id="{element_id}"' in html
    assert "fleet/policy/preview" in html
    assert "fleet/firmware/preview" in html
    assert "system/support-bundle" in html
