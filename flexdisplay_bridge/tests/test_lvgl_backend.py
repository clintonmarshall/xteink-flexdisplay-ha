from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.button_actions import (
    ButtonActionValidationError,
    normalize_action,
)
from flexdisplay_bridge.config import (
    BridgeConfig,
    DashboardPageConfig,
    DashboardProfileConfig,
    EntityConfig,
    load_config,
)
from flexdisplay_bridge.display_profiles import (
    JC3636_PROFILE,
    DisplayProfileStateError,
    DisplayProfileStore,
)
from flexdisplay_bridge.home_assistant import HomeAssistantClient
from flexdisplay_bridge.lvgl_manifest import (
    LVGL_UI_MEDIA_TYPE,
    MAX_LVGL_MANIFEST_BYTES,
)
from flexdisplay_bridge.receiver_auth import derive_receiver_key
from flexdisplay_bridge.receiver_credentials import ReceiverCredentialStateError
from flexdisplay_bridge.store import DeviceStore, DeviceStoreStateError


MASTER = "bridge-only-lvgl-master-secret"
BOOT_ID = "0123456789abcdef0123456789abcdef"
DEVICE_ID = "JC36-A1B2C3D4E5F6"


def _profile(*, action: dict | None = None) -> DashboardProfileConfig:
    return DashboardProfileConfig(
        name="default",
        color_theme="ocean",
        pages=(
            DashboardPageConfig(
                title="Overview",
                entities=(
                    EntityConfig(
                        "static.temperature",
                        "Outside",
                        unit="°C",
                        source="static",
                        value="21.5",
                        tap_action=action or {"type": "none"},
                    ),
                ),
            ),
        ),
    )


def _config(tmp_path: Path, *, action: dict | None = None, api_key: str = "") -> BridgeConfig:
    return BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key=api_key,
        receiver_key_master=MASTER,
        profiles={"default": _profile(action=action)},
        default_profile="default",
    )


def _headers(
    device_id: str = DEVICE_ID,
    *,
    model: str = "JC3636W518EN",
    width: int = 360,
    height: int = 360,
    master: str = MASTER,
    key_for: str | None = None,
    boot_id: str = BOOT_ID,
) -> dict[str, str]:
    return {
        "Accept": LVGL_UI_MEDIA_TYPE,
        "X-FlexDisplay-ID": device_id,
        "X-FlexDisplay-Device-Key": derive_receiver_key(
            master, key_for or device_id
        ),
        "X-FlexDisplay-Model": model,
        "X-FlexDisplay-Width": str(width),
        "X-FlexDisplay-Height": str(height),
        "X-FlexDisplay-Capabilities": "lvgl-ui-v1,touch,rgb565",
        "X-FlexDisplay-Boot-ID": boot_id,
    }


def _enroll(client: TestClient, headers: dict[str, str] | None = None) -> dict:
    response = client.get("/api/v1/screen", headers=headers or _headers())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == LVGL_UI_MEDIA_TYPE
    assert len(response.content) <= MAX_LVGL_MANIFEST_BYTES
    assert "x-flexdisplay-firmware-url" not in response.headers
    assert "x-flexdisplay-image-sha256" not in response.headers
    return response.json()


def _event(manifest: dict, sequence: object = 1, version: object = 1) -> dict:
    tile = manifest["pages"][0]["tiles"][0]
    numeric_sequence = sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else 1
    return {
        "version": version,
        "event_id": f"{BOOT_ID}-{numeric_sequence:08X}",
        "session_id": BOOT_ID,
        "sequence": sequence,
        "manifest_revision": manifest["revision"],
        "page_id": tile["page_id"],
        "tile_id": tile["id"],
        "gesture": "tap",
        "action_id": tile["action_id"],
    }


def _event_headers(device_id: str = DEVICE_ID, *, key_for: str | None = None) -> dict[str, str]:
    return {
        "X-FlexDisplay-ID": device_id,
        "X-FlexDisplay-Boot-ID": BOOT_ID,
        "X-FlexDisplay-Device-Key": derive_receiver_key(MASTER, key_for or device_id),
    }


def test_jc_profile_uses_exact_hardware_identity() -> None:
    assert JC3636_PROFILE.resolution == (360, 360)
    assert JC3636_PROFILE.display_controller == "ST77916"
    assert JC3636_PROFILE.touch_controller == "CST816S"


def test_lvgl_enrollment_requires_exact_model_not_resolution_only(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/screen",
            headers=_headers(device_id="SPOOF-360", model="UNKNOWN-ROUND"),
        )
    assert response.status_code == 409
    assert app.state.store.get("SPOOF-360") is None


def test_jc_enrollment_requires_canonical_full_mac_id(tmp_path: Path) -> None:
    device_id = "JC36-A1B2C3"
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/screen",
            headers=_headers(device_id=device_id),
        )
    assert response.status_code == 409
    assert app.state.store.get(device_id) is None


def test_lowercase_jc_namespace_never_falls_through_to_legacy_screen(
    tmp_path: Path,
) -> None:
    device_id = DEVICE_ID.lower()
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": device_id,
                "X-FlexDisplay-Width": "360",
                "X-FlexDisplay-Height": "360",
            },
        )
    assert response.status_code == 400
    assert "x-flexdisplay-firmware-url" not in response.headers
    assert "x-flexdisplay-image-sha256" not in response.headers
    assert app.state.store.get(device_id) is None


def test_valid_receiver_key_alone_cannot_downgrade_to_legacy_screen(
    tmp_path: Path,
) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "CUSTOM-A1B2C3D4E5F6",
                "X-FlexDisplay-Device-Key": derive_receiver_key(
                    MASTER, "CUSTOM-A1B2C3D4E5F6"
                ),
                "X-FlexDisplay-Width": "360",
                "X-FlexDisplay-Height": "360",
            },
        )
    assert response.status_code == 406
    assert "x-flexdisplay-firmware-url" not in response.headers
    assert "x-flexdisplay-image-sha256" not in response.headers
    assert app.state.store.get("CUSTOM-A1B2C3D4E5F6") is None


def test_custom_profile_cannot_bypass_jc36_full_mac_identity(tmp_path: Path) -> None:
    device_id = "JC36-A1B2C3"
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        saved = client.put(
            "/api/v1/display-profiles/custom_round",
            json={
                "model": "CUSTOM_ROUND",
                "display_name": "Custom round",
                "technology": "color",
                "width": 360,
                "height": 360,
                "shape": "round",
                "pixel_format": "RGB565",
                "color_depth": 16,
                "touch": False,
                "lvgl": True,
                "display_controller": "TEST",
            },
        )
        assert saved.status_code == 200, saved.text
        attempted = client.get(
            "/api/v1/screen",
            headers=_headers(
                device_id=device_id,
                model="CUSTOM_ROUND",
            ),
        )
    assert attempted.status_code == 409
    assert app.state.store.get(device_id) is None


def test_xteink_lvgl_request_never_falls_through_to_image_or_firmware(tmp_path: Path) -> None:
    device_id = "X3-C0FFEE"
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/screen",
            headers=_headers(
                device_id=device_id,
                model="X3",
                width=528,
                height=792,
            ),
        )
    assert response.status_code == 409
    assert "x-flexdisplay-firmware-url" not in response.headers
    assert "x-flexdisplay-image-sha256" not in response.headers
    assert app.state.store.get(device_id) is None


@pytest.mark.parametrize("known_model", ["N4", "ROOK", "CHECKERS", "X3", "X4"])
def test_known_non_lvgl_families_cannot_enroll_via_custom_profile(
    tmp_path: Path, known_model: str
) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        saved = client.put(
            "/api/v1/display-profiles/custom_round",
            json={
                "model": known_model,
                "display_name": "Unsafe override",
                "technology": "color",
                "width": 360,
                "height": 360,
                "shape": "round",
                "pixel_format": "RGB565",
                "color_depth": 16,
                "touch": False,
                "lvgl": True,
                "display_controller": "TEST",
            },
        )
    assert saved.status_code == 400


@pytest.mark.parametrize(
    ("device_id", "original_model", "width", "height"),
    [
        ("N4-BOUND01", "N4", 400, 300),
        ("ROOK-BOUND01", "ROOK", 480, 480),
        ("CHECKERS-BOUND01", "CHECKERS", 960, 480),
    ],
)
def test_existing_known_family_cannot_be_reprovisioned_as_custom_lvgl(
    tmp_path: Path,
    device_id: str,
    original_model: str,
    width: int,
    height: int,
) -> None:
    app = create_app(_config(tmp_path))
    receiver_headers = (
        {"X-FlexDisplay-Receiver-Token": "bound-receiver-token"}
        if original_model in {"ROOK", "CHECKERS"}
        else {}
    )
    with TestClient(app) as client:
        original = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": device_id,
                "X-FlexDisplay-Model": original_model,
                "X-FlexDisplay-Width": str(width),
                "X-FlexDisplay-Height": str(height),
                **receiver_headers,
            },
        )
        assert original.status_code == 200
        saved = client.put(
            "/api/v1/display-profiles/custom_panel",
            json={
                "model": "CUSTOM_PANEL",
                "display_name": "Custom panel",
                "technology": "color",
                "width": width,
                "height": height,
                "shape": "round" if width == height else "rect",
                "pixel_format": "RGB565",
                "color_depth": 16,
                "touch": False,
                "lvgl": True,
                "display_controller": "TEST",
            },
        )
        assert saved.status_code == 200, saved.text
        attempted = client.get(
            "/api/v1/screen",
            headers={
                **_headers(
                    device_id=device_id,
                    model="CUSTOM_PANEL",
                    width=width,
                    height=height,
                ),
                **receiver_headers,
            },
        )
    assert attempted.status_code == 409
    record = app.state.store.get(device_id)
    assert record["model"] == original_model


def test_extracted_receiver_key_cannot_impersonate_sibling(tmp_path: Path) -> None:
    sibling = "JC36-001122334455"
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        _enroll(client)
        attempted = client.get(
            "/api/v1/screen",
            headers=_headers(sibling, key_for=DEVICE_ID),
        )
    assert attempted.status_code == 401
    assert app.state.store.get(sibling) is None
    assert app.state.store.get(DEVICE_ID)["boot_id"] == BOOT_ID


def test_derived_receiver_key_is_device_bound_lower_hex() -> None:
    first = derive_receiver_key(MASTER, DEVICE_ID)
    second = derive_receiver_key(MASTER, "JC36-001122334455")
    assert len(first) == 64
    assert first == first.lower()
    assert int(first, 16) >= 0
    assert first != second


def test_receiver_master_must_be_distinct_from_admin_key(tmp_path: Path) -> None:
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        api_key=MASTER,
        receiver_key_master=MASTER,
        profiles={"default": _profile()},
    )
    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/screen", headers=_headers())
    assert response.status_code == 503


def test_revoked_receiver_cannot_reenroll_or_use_receiver_routes(tmp_path: Path) -> None:
    app = create_app(
        _config(
            tmp_path,
            api_key="admin-bridge-secret",
            action={"type": "navigation", "command": "next"},
        )
    )
    with TestClient(app) as client:
        manifest = _enroll(client)
        revoked = client.post(
            f"/api/v1/receiver-credentials/{DEVICE_ID}/revoke",
            headers={"X-FlexDisplay-Bridge-Key": "admin-bridge-secret"},
        )
        assert revoked.status_code == 200
        app.state.store.remove_device(DEVICE_ID)
        reenroll = client.get("/api/v1/screen", headers=_headers())
        event = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers=_event_headers(),
            json=_event(manifest),
        )
        asset = client.get(
            f"/api/v1/devices/{DEVICE_ID}/ui-assets/{'a' * 24}.png",
            headers={"X-FlexDisplay-Device-Key": derive_receiver_key(MASTER, DEVICE_ID)},
        )
    assert (reenroll.status_code, event.status_code, asset.status_code) == (401, 401, 401)
    assert app.state.store.get(DEVICE_ID) is None


def test_rotated_epoch_invalidates_old_receiver_key(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path, api_key="admin-bridge-secret"))
    with TestClient(app) as client:
        rotated = client.post(
            f"/api/v1/receiver-credentials/{DEVICE_ID}/rotate",
            headers={"X-FlexDisplay-Bridge-Key": "admin-bridge-secret"},
        )
        assert rotated.status_code == 200
        old = client.get("/api/v1/screen", headers=_headers())
        current_headers = _headers()
        current_headers["X-FlexDisplay-Device-Key"] = derive_receiver_key(
            MASTER, DEVICE_ID, 1
        )
        current = client.get("/api/v1/screen", headers=current_headers)
    assert old.status_code == 401
    assert current.status_code == 200


@pytest.mark.parametrize("operation", ["revoke", "rotate"])
def test_receiver_admin_case_alias_targets_canonical_id(
    tmp_path: Path, operation: str
) -> None:
    app = create_app(_config(tmp_path, api_key="admin-bridge-secret"))
    with TestClient(app) as client:
        changed = client.post(
            f"/api/v1/receiver-credentials/{DEVICE_ID.lower()}/{operation}",
            headers={"X-FlexDisplay-Bridge-Key": "admin-bridge-secret"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["device_id"] == DEVICE_ID
        old_key = client.get("/api/v1/screen", headers=_headers())
    assert old_key.status_code == 401
    assert list(app.state.receiver_credentials.all()) == [DEVICE_ID]


def test_receiver_credential_state_rejects_duplicate_case_aliases(
    tmp_path: Path,
) -> None:
    credential_path = tmp_path / "flexdisplay-receiver-credentials.json"
    credential_path.write_text(
        json.dumps(
            {
                "version": 1,
                "receivers": {
                    DEVICE_ID: {"epoch": 0, "disabled": False},
                    DEVICE_ID.lower(): {"epoch": 1, "disabled": True},
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReceiverCredentialStateError):
        create_app(_config(tmp_path))


@pytest.mark.parametrize("field", ["version", "sequence"])
@pytest.mark.parametrize("invalid", [True, 1.0, "1"])
def test_ui_event_requires_json_integer_types(
    tmp_path: Path, field: str, invalid: object
) -> None:
    action = {"type": "navigation", "command": "next"}
    with TestClient(create_app(_config(tmp_path, action=action))) as client:
        manifest = _enroll(client)
        payload = _event(manifest)
        payload[field] = invalid
        response = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers=_event_headers(),
            json=payload,
        )
    assert response.status_code == 400


def test_ui_event_invalid_utf8_surrogate_is_controlled_400(tmp_path: Path) -> None:
    action = {"type": "navigation", "command": "next"}
    with TestClient(create_app(_config(tmp_path, action=action))) as client:
        manifest = _enroll(client)
        payload = _event(manifest)
        encoded = json.dumps(payload, separators=(",", ":")).encode("ascii")
        encoded = encoded[:-1] + b',"invalid":"\\ud800"}'
        response = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers={**_event_headers(), "Content-Type": "application/json"},
            content=encoded,
        )
    assert response.status_code == 400
    assert "UTF-8" in response.json()["detail"]


def test_ui_event_rejects_oversized_body_before_json_parse(tmp_path: Path) -> None:
    action = {"type": "navigation", "command": "next"}
    with TestClient(create_app(_config(tmp_path, action=action))) as client:
        _enroll(client)
        response = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers={**_event_headers(), "Content-Type": "application/json"},
            content=b"{" + b" " * 4096 + b"}",
        )
    assert response.status_code == 413


def test_ui_event_replay_is_session_revision_and_sequence_bound(tmp_path: Path) -> None:
    action = {"type": "navigation", "command": "next"}
    with TestClient(create_app(_config(tmp_path, action=action))) as client:
        manifest = _enroll(client)
        gap = _event(manifest, sequence=2)
        first = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers=_event_headers(),
            json=gap,
        )
        duplicate = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers=_event_headers(),
            json=gap,
        )
        old = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers=_event_headers(),
            json=_event(manifest, sequence=1),
        )
        stale_payload = _event(manifest, sequence=3)
        stale_payload["manifest_revision"] = "0" * 24
        stale = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers=_event_headers(),
            json=stale_payload,
        )
    assert first.status_code == 200
    assert first.json()["command"] == "next"
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert old.status_code == 409
    assert stale.status_code == 409


def test_event_executes_exact_action_bound_to_delivered_manifest(tmp_path: Path) -> None:
    action = {"type": "navigation", "command": "next"}
    app = create_app(_config(tmp_path, action=action))
    with TestClient(app) as client:
        manifest = _enroll(client)
        changed = client.put(
            "/api/v1/studio/profiles/default",
            json={
                "pages": [
                    {
                        "title": "Overview",
                        "entities": [
                            {
                                "entity_id": "static.temperature",
                                "label": "Outside",
                                "source": "static",
                                "value": "21.5",
                                "tap_action": {
                                    "type": "navigation",
                                    "command": "previous",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        assert changed.status_code == 200, changed.text
        response = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers=_event_headers(),
            json=_event(manifest),
        )
    assert response.status_code == 200
    assert response.json()["command"] == "next"


def test_duplicate_pending_event_is_not_reexecuted(tmp_path: Path) -> None:
    action = {"type": "navigation", "command": "next"}
    app = create_app(_config(tmp_path, action=action))
    with TestClient(app) as client:
        manifest = _enroll(client)
        payload = _event(manifest)
        _, is_new, status = app.state.store.record_ui_event(DEVICE_ID, payload)
        assert is_new is True and status == "accepted"
        response = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers=_event_headers(),
            json=payload,
        )
    assert response.status_code == 409
    assert "will not be retried" in response.json()["detail"]
    assert "next" not in (app.state.store.get(DEVICE_ID).get("pending_commands") or [])


def test_duplicate_failed_event_reports_failure_without_retry(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple] = []

    def fail_service(self, service, entity_id="", data=None):
        del self
        calls.append((service, entity_id, data))
        return False, "simulated failure"

    monkeypatch.setattr(HomeAssistantClient, "call_service", fail_service)
    action = {
        "type": "home_assistant",
        "service": "light.turn_on",
        "entity_id": "light.office",
    }
    with TestClient(create_app(_config(tmp_path, action=action))) as client:
        manifest = _enroll(client)
        payload = _event(manifest)
        first = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers=_event_headers(),
            json=payload,
        )
        duplicate = client.post(
            f"/api/v1/devices/{DEVICE_ID}/ui-events",
            headers=_event_headers(),
            json=payload,
        )
    assert first.status_code == 502
    assert duplicate.status_code == 502
    assert "will not be retried" in duplicate.json()["detail"]
    assert len(calls) == 1


def test_read_only_tile_never_advertises_or_accepts_action_binding(tmp_path: Path) -> None:
    action = {"type": "navigation", "command": "next"}
    profile = _profile(action=action)
    read_only_tile = EntityConfig(
        **{
            **profile.pages[0].entities[0].__dict__,
            "control_style": "read_only",
        }
    )
    read_only_profile = DashboardProfileConfig(
        name="default",
        pages=(
            DashboardPageConfig(title="Overview", entities=(read_only_tile,)),
        ),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        receiver_key_master=MASTER,
        profiles={"default": read_only_profile},
    )
    app = create_app(config)
    with TestClient(app) as client:
        manifest = _enroll(client)
    assert "action_id" not in manifest["pages"][0]["tiles"][0]
    assert app.state.store.get(DEVICE_ID)["last_ui_manifest_actions"] == []


def test_manifest_has_final_serialized_64k_guard(tmp_path: Path, monkeypatch) -> None:
    import flexdisplay_bridge.app as app_module

    def oversized(*args, **kwargs):
        del args, kwargs
        return {
            "revision": "a" * 24,
            "page_count": 1,
            "pages": [],
            "oversized": "x" * MAX_LVGL_MANIFEST_BYTES,
        }

    monkeypatch.setattr(app_module, "build_lvgl_manifest", oversized)
    with TestClient(create_app(_config(tmp_path))) as client:
        response = client.get("/api/v1/screen", headers=_headers())
    assert response.status_code == 409
    assert "64 KiB" in response.json()["detail"]


def test_non_finite_service_data_is_rejected_before_manifest_hashing() -> None:
    with pytest.raises(ButtonActionValidationError):
        normalize_action(
            {
                "type": "home_assistant",
                "service": "light.turn_on",
                "data": {"brightness": float("nan")},
            }
        )


def test_deep_service_data_is_a_controlled_validation_error() -> None:
    nested: dict = {}
    selected = nested
    for _ in range(1200):
        child: dict = {}
        selected["child"] = child
        selected = child
    with pytest.raises(ButtonActionValidationError):
        normalize_action(
            {
                "type": "home_assistant",
                "service": "light.turn_on",
                "data": nested,
            }
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("service", f"light.{'a' * 130}"),
        ("entity_id", f"light.{'a' * 130}"),
    ],
)
def test_action_identifiers_have_explicit_byte_bounds(field: str, value: str) -> None:
    action = {
        "type": "home_assistant",
        "service": "light.turn_on",
        field: value,
    }
    with pytest.raises(ButtonActionValidationError, match="too long"):
        normalize_action(action)


def test_corrupt_display_profile_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "flexdisplay-display-profiles.json"
    path.write_text('{"version":1,"profiles":{"bad":', encoding="utf-8")
    with pytest.raises(DisplayProfileStateError, match="unreadable"):
        DisplayProfileStore(path)


def test_corrupt_device_replay_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"devices":', encoding="utf-8")
    with pytest.raises(DeviceStoreStateError, match="unreadable"):
        DeviceStore(path)


def test_display_profile_mutations_are_transactional_on_save_failure(
    tmp_path: Path, monkeypatch
) -> None:
    store = DisplayProfileStore(tmp_path / "profiles.json")
    payload = {
        "model": "TEST_PANEL",
        "display_name": "Test panel",
        "technology": "color",
        "width": 320,
        "height": 240,
        "shape": "rect",
        "pixel_format": "RGB565",
        "color_depth": 16,
        "touch": False,
        "lvgl": True,
        "display_controller": "TEST",
    }
    store.put("test_panel", payload)

    def fail_save(custom=None):
        del custom
        raise OSError("simulated disk failure")

    monkeypatch.setattr(store, "_save", fail_save)
    with pytest.raises(OSError, match="disk failure"):
        store.put(
            "second_panel",
            {
                **payload,
                "model": "SECOND_PANEL",
                "display_name": "Second panel",
            },
        )
    assert store.get("second_panel") is None
    assert store.get("test_panel") is not None
    with pytest.raises(OSError, match="disk failure"):
        store.delete("test_panel")
    assert store.get("test_panel") is not None


def test_custom_colour_preview_accepts_full_profile_bounds(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        saved = client.put(
            "/api/v1/display-profiles/wide_panel",
            json={
                "model": "WIDE_PANEL",
                "display_name": "Wide panel",
                "technology": "color",
                "width": 2048,
                "height": 128,
                "shape": "rect",
                "pixel_format": "RGB565",
                "color_depth": 16,
                "touch": False,
                "lvgl": True,
                "display_controller": "TEST",
            },
        )
        assert saved.status_code == 200, saved.text
        preview = client.post(
            "/api/v1/studio/preview",
            json={
                "model": "WIDE_PANEL",
                "width": 2048,
                "height": 128,
                "profile": {
                    "pages": [
                        {
                            "title": "Wide",
                            "entities": [
                                {
                                    "entity_id": "static.message",
                                    "label": "Message",
                                    "source": "static",
                                    "value": "Ready",
                                }
                            ],
                        }
                    ]
                },
            },
        )
    assert preview.status_code == 200, preview.text
    assert preview.headers["x-flexdisplay-preview-renderer"] == "lvgl-color"
    with Image.open(__import__("io").BytesIO(preview.content)) as image:
        assert image.size == (2048, 128)


def test_colour_preview_uses_canonical_role_and_control_affordances(
    tmp_path: Path,
) -> None:
    app = create_app(_config(tmp_path))

    def profile(
        *,
        color_role: str,
        control_style: str,
        tap_action: dict | None = None,
        value: str = "Ready",
    ) -> dict:
        return {
            "color_theme": "ocean",
            "pages": [
                {
                    "title": "Control",
                    "layout": "single",
                    "entities": [
                        {
                            "entity_id": "static.control",
                            "label": "Control",
                            "source": "static",
                            "value": value,
                            "color_role": color_role,
                            "control_style": control_style,
                            "tap_action": tap_action or {"type": "none"},
                        }
                    ],
                }
            ],
        }

    def preview(client: TestClient, selected: dict) -> bytes:
        response = client.post(
            "/api/v1/studio/preview",
            json={"model": "JC3636W518EN", "profile": selected},
        )
        assert response.status_code == 200, response.text
        assert response.headers["x-flexdisplay-preview-renderer"] == "lvgl-color"
        return response.content

    with TestClient(app) as client:
        primary_status = preview(
            client,
            profile(color_role="primary", control_style="read_only"),
        )
        danger_status = preview(
            client,
            profile(color_role="danger", control_style="read_only"),
        )
        button = preview(
            client,
            profile(
                color_role="primary",
                control_style="button",
                tap_action={"type": "navigation", "command": "next"},
            ),
        )
        toggle_on = preview(
            client,
            profile(
                color_role="success",
                control_style="toggle",
                tap_action={
                    "type": "home_assistant",
                    "service": "homeassistant.toggle",
                    "entity_id": "light.preview",
                },
                value="on",
            ),
        )

    assert primary_status != danger_status
    assert primary_status != button
    assert button != toggle_on


def test_non_touch_profile_gets_static_manifest_but_cannot_post_actions(
    tmp_path: Path,
) -> None:
    device_id = "PANEL-001122"
    action = {"type": "navigation", "command": "next"}
    app = create_app(_config(tmp_path, action=action))
    with TestClient(app) as client:
        saved = client.put(
            "/api/v1/display-profiles/non_touch",
            json={
                "model": "NON_TOUCH",
                "display_name": "Non touch",
                "technology": "color",
                "width": 320,
                "height": 240,
                "shape": "rect",
                "pixel_format": "RGB565",
                "color_depth": 16,
                "touch": False,
                "lvgl": True,
                "display_controller": "TEST",
            },
        )
        assert saved.status_code == 200
        headers = _headers(
            device_id=device_id,
            model="NON_TOUCH",
            width=320,
            height=240,
        )
        headers["X-FlexDisplay-Capabilities"] = "lvgl-ui-v1,rgb565"
        delivered = client.get("/api/v1/screen", headers=headers)
        assert delivered.status_code == 200, delivered.text
        manifest = delivered.json()
        assert "action_id" not in manifest["pages"][0]["tiles"][0]
        missing_asset = client.get(
            f"/api/v1/devices/{device_id}/ui-assets/{'a' * 24}.png",
            headers={
                "X-FlexDisplay-Device-Key": derive_receiver_key(MASTER, device_id)
            },
        )
        assert missing_asset.status_code == 404
        attempted = client.post(
            f"/api/v1/devices/{device_id}/ui-events",
            headers={
                "X-FlexDisplay-ID": device_id,
                "X-FlexDisplay-Boot-ID": BOOT_ID,
                "X-FlexDisplay-Device-Key": derive_receiver_key(MASTER, device_id),
            },
            json={
                "version": 1,
                "event_id": f"{BOOT_ID}-00000001",
                "session_id": BOOT_ID,
                "sequence": 1,
                "manifest_revision": manifest["revision"],
                "page_id": "page-1",
                "tile_id": "tile-1-1",
                "gesture": "tap",
                "action_id": "a" * 24,
            },
        )
    assert attempted.status_code == 409


def test_colour_preview_uses_selected_manifest_theme(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        previews = []
        for theme in ("ocean", "paper"):
            response = client.post(
                "/api/v1/studio/preview",
                json={
                    "model": "JC3636W518EN",
                    "profile": {
                        "color_theme": theme,
                        "pages": [
                            {
                                "title": "Theme",
                                "entities": [
                                    {
                                        "entity_id": "static.message",
                                        "label": "Message",
                                        "source": "static",
                                        "value": "Ready",
                                    }
                                ],
                            }
                        ],
                    },
                },
            )
            assert response.status_code == 200, response.text
            previews.append(response.content)
    assert previews[0] != previews[1]


@pytest.mark.parametrize("style", ["history", "qr", "image", "name_card"])
def test_lvgl_preview_rejects_visuals_the_receiver_does_not_render(
    tmp_path: Path,
    style: str,
    monkeypatch,
) -> None:
    entity = {
        "entity_id": "image.preview" if style == "image" else "static.preview",
        "label": "Preview",
        "style": style,
        "source": "home_assistant" if style == "image" else "static",
        **({} if style == "image" else {"value": "Same content"}),
    }
    if style == "image":
        def unexpected_fetch(*args, **kwargs):
            del args, kwargs
            raise AssertionError("unsupported LVGL image must be rejected before fetch")

        monkeypatch.setattr(HomeAssistantClient, "fetch", unexpected_fetch)
    with TestClient(create_app(_config(tmp_path))) as client:
        response = client.post(
            "/api/v1/studio/preview",
            json={
                "model": "JC3636W518EN",
                "profile": {"pages": [{"title": "Unsupported", "entities": [entity]}]},
            },
        )
    assert response.status_code == 400
    assert style in response.json()["detail"]
    assert "LVGL receiver v1 does not support" in response.json()["detail"]


def test_lvgl_preview_rejects_house_pulse_before_entity_fetch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_fetch(*args, **kwargs):
        del args, kwargs
        raise AssertionError("unsupported LVGL layout must be rejected before fetch")

    monkeypatch.setattr(HomeAssistantClient, "fetch", unexpected_fetch)
    with TestClient(create_app(_config(tmp_path))) as client:
        response = client.post(
            "/api/v1/studio/preview",
            json={
                "model": "JC3636W518EN",
                "profile": {
                    "pages": [
                        {
                            "title": "House Pulse",
                            "layout": "house_pulse",
                            "entities": [
                                {
                                    "entity_id": "sensor.site_power",
                                    "label": "Site power",
                                }
                            ],
                        }
                    ]
                },
            },
        )
    assert response.status_code == 400
    assert "house_pulse page layout" in response.json()["detail"]


def test_lvgl_device_delivery_rejects_unsupported_profile_style(tmp_path: Path) -> None:
    profile = DashboardProfileConfig(
        name="default",
        pages=(
            DashboardPageConfig(
                title="Unsupported",
                entities=(
                    EntityConfig(
                        "static.qr",
                        "QR",
                        style="qr",
                        source="static",
                        value="Same content",
                    ),
                ),
            ),
        ),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        receiver_key_master=MASTER,
        profiles={"default": profile},
        default_profile="default",
    )
    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/screen", headers=_headers())
    assert response.status_code == 409
    assert "LVGL receiver v1 does not support the qr tile style" in response.json()["detail"]


def test_lvgl_device_delivery_rejects_house_pulse_before_entity_fetch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_fetch(*args, **kwargs):
        del args, kwargs
        raise AssertionError("unsupported LVGL layout must be rejected before fetch")

    monkeypatch.setattr(HomeAssistantClient, "fetch", unexpected_fetch)
    profile = DashboardProfileConfig(
        name="default",
        pages=(
            DashboardPageConfig(
                title="House Pulse",
                layout="house_pulse",
                entities=(EntityConfig("sensor.site_power", "Site power"),),
            ),
        ),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        receiver_key_master=MASTER,
        profiles={"default": profile},
        default_profile="default",
    )
    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/screen", headers=_headers())
    assert response.status_code == 409
    assert "house_pulse page layout" in response.json()["detail"]


def test_lvgl_manifest_omits_eink_only_scale_badge_and_asset_fields(
    tmp_path: Path,
) -> None:
    profile = DashboardProfileConfig(
        name="default",
        pages=(
            DashboardPageConfig(
                title="Supported",
                entities=(
                    EntityConfig(
                        "static.value",
                        "Value",
                        source="static",
                        value="Same content",
                        badge_theme="halftone",
                        badge_photo_id="a" * 24,
                        text_scale=180,
                        qr_scale=150,
                    ),
                ),
            ),
        ),
    )
    config = BridgeConfig(
        state_path=tmp_path / "state.json",
        receiver_key_master=MASTER,
        profiles={"default": profile},
        default_profile="default",
    )
    with TestClient(create_app(config)) as client:
        manifest = _enroll(client)
        studio = client.get("/api/v1/studio").json()
    tile = manifest["pages"][0]["tiles"][0]
    for field in (
        "text_scale",
        "qr_scale",
        "badge_theme",
        "asset",
        "content",
        "history",
        "image",
        "icon",
    ):
        assert field not in tile
    assert studio["capabilities"]["lvgl_styles"] == ["gauge", "progress", "value"]
    assert studio["capabilities"]["lvgl_layouts"] == [
        "auto",
        "single",
        "rows",
        "columns",
        "grid",
    ]
    assert studio["capabilities"]["lvgl_icons"] == []


def test_eink_preview_keeps_distinct_qr_and_honours_exposed_scales(
    tmp_path: Path,
) -> None:
    def preview(
        client: TestClient,
        *,
        style: str,
        text_scale: int = 100,
        qr_scale: int = 100,
    ) -> bytes:
        response = client.post(
            "/api/v1/studio/preview",
            json={
                "model": "X4",
                "profile": {
                    "pages": [
                        {
                            "title": "Supported",
                            "layout": "single",
                            "entities": [
                                {
                                    "entity_id": "static.same",
                                    "label": "Same",
                                    "style": style,
                                    "source": "static",
                                    "value": "https://example.com/same",
                                    "text_scale": text_scale,
                                    "qr_scale": qr_scale,
                                }
                            ],
                        }
                    ]
                },
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["x-flexdisplay-preview-renderer"] == "bitmap"
        return response.content

    with TestClient(create_app(_config(tmp_path))) as client:
        value = preview(client, style="value")
        qr_small = preview(client, style="qr", qr_scale=50)
        qr_large = preview(client, style="qr", qr_scale=150)
        text_small = preview(client, style="value", text_scale=60)
        text_large = preview(client, style="value", text_scale=180)

    assert value != qr_small
    assert qr_small != qr_large
    assert text_small != text_large


def test_corrupt_optional_profile_state_disables_only_colour_subsystem(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    state_path.with_name("flexdisplay-display-profiles.json").write_text(
        '{"profiles":', encoding="utf-8"
    )
    app = create_app(BridgeConfig(state_path=state_path, receiver_key_master=MASTER))
    with TestClient(app) as client:
        legacy = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "X3-LEGACY1",
                "X-FlexDisplay-Model": "X3",
                "X-FlexDisplay-Width": "528",
                "X-FlexDisplay-Height": "792",
            },
        )
        colour = client.get("/api/v1/screen", headers=_headers())
        health = client.get("/healthz").json()
    assert legacy.status_code == 200
    assert colour.status_code == 503
    assert health["color_display_profiles"]["available"] is False


def test_yaml_profiles_enforce_lvgl_page_tile_and_field_bounds(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dashboard:
  profiles:
    bounded:
      color_theme: ocean
      pages:
        - title: This title is deliberately much longer than forty eight characters in YAML
          entities:
            - entity_id: static.message
              label: Message
              source: static
              value: Ready
              style: value
              icon: auto
              color_role: info
              control_style: read_only
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.delenv("FLEXDISPLAY_CONFIG", raising=False)
    settings = load_config(config_path)
    profile = settings.profiles["bounded"]
    tile = profile.pages[0].entities[0]
    assert len(profile.pages[0].title) == 48
    assert tile.style == "value"
    assert tile.icon == "auto"
    assert tile.color_role == "info"
    assert tile.control_style == "read_only"

    invalid = config_path.read_text(encoding="utf-8").replace(
        "              style: value", "              style: unsupported"
    )
    config_path.write_text(invalid, encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported style"):
        load_config(config_path)

    config_path.write_text(invalid.replace("style: unsupported", "style: value"), encoding="utf-8")
    too_many = config_path.read_text(encoding="utf-8").replace(
        "              control_style: read_only",
        """              control_style: read_only
            - static.two
            - static.three
            - static.four
            - static.five""",
    )
    config_path.write_text(too_many, encoding="utf-8")
    with pytest.raises(ValueError, match="at most four tiles"):
        load_config(config_path)


def test_yaml_profile_preserves_entity_shorthand_and_empty_profile_compatibility(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dashboard:
  profiles:
    shorthand:
      pages:
        - title: Home
          entities:
            - sensor.temperature
    empty:
      color_theme: ocean
      auto_rotate_seconds: 30
      pages: []
""".strip(),
        encoding="utf-8",
    )
    settings = load_config(config_path)
    shorthand = settings.profiles["shorthand"].pages[0].entities[0]
    assert shorthand.entity_id == "sensor.temperature"
    assert shorthand.label == "sensor.temperature"
    assert settings.profiles["empty"].pages == ()
    assert settings.profiles["empty"].color_theme == "ocean"
    assert settings.profiles["empty"].auto_rotate_seconds == 30


def test_receiver_master_loads_only_from_owner_only_regular_file(
    tmp_path: Path,
) -> None:
    master_path = tmp_path / "receiver-master"
    master_path.write_text("  exact master with spaces  ", encoding="utf-8")
    master_path.chmod(0o600)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"server:\n  lvgl_receiver_key_master_file: {master_path}\n",
        encoding="utf-8",
    )
    settings = load_config(config_path)
    assert settings.receiver_key_master == "  exact master with spaces  "

    link = tmp_path / "receiver-link"
    link.symlink_to(master_path)
    config_path.write_text(
        f"server:\n  lvgl_receiver_key_master_file: {link}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="symlink"):
        load_config(config_path)
