"""HA action and client contracts, with real schema validation and no BLE I/O."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol


@pytest.fixture
def modules(monkeypatch):
    root = Path(__file__).parents[2] / "custom_components/flexdisplay"
    package = ModuleType("image_service_test")
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    for name, attrs in {
        "homeassistant": {}, "homeassistant.helpers": {},
        "homeassistant.core": {"SupportsResponse": SimpleNamespace(ONLY="only", OPTIONAL="optional")},
        "homeassistant.exceptions": {"ServiceValidationError": type("ServiceValidationError", (Exception,), {})},
        "homeassistant.helpers.config_validation": {"string": str},
        "homeassistant.helpers.device_registry": {},
        "homeassistant.config_entries": {"ConfigEntryState": SimpleNamespace(LOADED="loaded")},
        "image_service_test.const": {"DOMAIN": "flexdisplay"},
        "aiohttp": {"ClientError": Exception, "ClientResponseError": Exception, "ClientSession": object},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    loaded = []
    for filename in ("api", "top52810_services"):
        spec = importlib.util.spec_from_file_location(f"image_service_test.{filename}", root / f"{filename}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded


def test_actions_preview_send_and_schema(modules):
    api, services = modules
    registered = {}
    hass = SimpleNamespace(services=SimpleNamespace(
        has_service=lambda domain, name: name in registered,
        async_register=lambda domain, name, handler, **kwargs: registered.update({name: (handler, kwargs)}),
    ))
    client = SimpleNamespace(preview_top52810_image=AsyncMock(return_value={"device_io": False}),
                             send_top52810_image=AsyncMock(return_value={"job_id": "test"}))
    services.register_image_services(hass, lambda h, call: SimpleNamespace(client=client))
    services.register_image_services(hass, lambda h, call: SimpleNamespace(client=client))
    assert len(registered) == 3
    client.send_top52810_image.assert_not_called()
    preview, config = registered["preview_top52810_image"]
    assert config["supports_response"] == "only"
    call = SimpleNamespace(data=config["schema"]({"image_base64": "AAAA"}))
    assert asyncio.run(preview(call)) == {"device_io": False}
    client.send_top52810_image.assert_not_called()
    send, config = registered["send_top52810_image"]
    for data in ({"image_base64": "AAAA"}, {"image_base64": "AAAA", "expected_plan_sha256": "bad"},
                 {"image_base64": "A" * 174765, "expected_plan_sha256": "a" * 64}):
        with pytest.raises(vol.Invalid):
            config["schema"](data)
    call.data = config["schema"]({"image_base64": "AAAA", "expected_plan_sha256": "a" * 64})
    assert asyncio.run(send(call)) == {"job_id": "test"}
    client.send_top52810_image.side_effect = api.FlexDisplayApiError("rejected")
    with pytest.raises(services.ServiceValidationError, match="rejected"):
        asyncio.run(send(call))


def test_client_never_confirms_changed_preview_and_filters_private_job_fields(modules):
    api, _ = modules
    client = api.FlexDisplayApiClient(None, "http://test")
    client._request = AsyncMock(return_value={"plan_sha256": "b" * 64, "write_count": 44, "device_io": False})
    with pytest.raises(api.FlexDisplayApiError):
        asyncio.run(client.send_top52810_image("AAAA", "a" * 64))
    assert client._request.await_count == 1
    client._request = AsyncMock(side_effect=[
        {"plan_sha256": "a" * 64, "write_count": 44, "device_io": False},
        {"job_id": "test", "frames": ["image bytes"], "lease": "private"},
    ])
    assert asyncio.run(client.send_top52810_image("AAAA", "a" * 64)) == {"job_id": "test"}
    payload = client._request.call_args.kwargs["json"]
    assert payload["address"] == "DF:84:6B:DE:F6:ED"
    assert payload["reject_if_active"] is True and payload["expires_seconds"] == 900


def test_file_actions_and_exclusive_inputs(modules, monkeypatch):
    api, services = modules
    registered = {}
    client = SimpleNamespace(
        prepare_top52810_image=AsyncMock(return_value={"image_base64": "AAAA", "plan_sha256": "a" * 64}),
        preview_top52810_image=AsyncMock(),
        send_top52810_image=AsyncMock(return_value={"job_id": "test"}))
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=b"png"),
        services=SimpleNamespace(has_service=lambda *args: False,
            async_register=lambda domain, name, handler, **kw: registered.update({name: (handler, kw)})))
    services.register_image_services(hass, lambda *args: SimpleNamespace(client=client))
    preview, config = registered["preview_top52810_image"]
    with pytest.raises(vol.Invalid):
        config["schema"]({"image_file": "/media/dog.png", "image_base64": "AAAA"})
    call = SimpleNamespace(data=config["schema"]({"image_file": "/media/dog.png", "resize_mode": "crop"}))
    assert asyncio.run(preview(call))["plan_sha256"] == "a" * 64
    client.prepare_top52810_image.assert_awaited_once_with(b"png", "crop")
    client.send_top52810_image.assert_not_called()
    send, config = registered["send_top52810_image"]
    call.data = config["schema"]({"image_file": "/media/dog.png", "expected_plan_sha256": "a" * 64})
    assert asyncio.run(send(call)) == {"job_id": "test"}
    client.send_top52810_image.assert_awaited_once_with("AAAA", "a" * 64)
    hass.async_add_executor_job.side_effect = OSError("not a regular file")
    with pytest.raises(services.ServiceValidationError):
        asyncio.run(preview(call))


def test_media_read_cannot_escape_root(modules, monkeypatch, tmp_path):
    import os
    media = sys.modules["image_service_test.top52810_media"]
    original_open = os.open
    def redirected(path, flags, **kwargs):
        return original_open(str(tmp_path) if path == "/media" else path, flags, **kwargs)
    monkeypatch.setattr(media.os, "open", redirected)
    (tmp_path / "dog.png").write_bytes(b"png")
    assert media.read_media_image("/media/dog.png") == b"png"
    for invalid in ("/config/secrets.yaml", "/media/../secret", "https://example.com/dog.png", "/media"):
        with pytest.raises(ValueError):
            media.read_media_image(invalid)
    (tmp_path / "link.png").symlink_to(tmp_path / "dog.png")
    (tmp_path / "subdir").symlink_to(tmp_path, target_is_directory=True)
    for path in ("/media/link.png", "/media/subdir/dog.png"):
        with pytest.raises(OSError):
            media.read_media_image(path)
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(ValueError):
        media.read_media_image("/media/pipe")


@pytest.fixture
def friendly(modules, monkeypatch):
    _, services = modules
    address = "DF:84:6B:DE:F6:ED"
    entity = ModuleType("image_service_test.top52810_entity")
    entity.ADDRESS = address
    entity.admitted = lambda record: record.get("admitted") is True
    monkeypatch.setitem(sys.modules, entity.__name__, entity)
    client = SimpleNamespace(
        prepare_top52810_image=AsyncMock(return_value={
            "image_base64": "AAAA", "plan_sha256": "a" * 64,
            "write_count": 44, "device_io": False}),
        send_top52810_image=AsyncMock(return_value={"job_id": "queued"}),
    )
    tag = SimpleNamespace(client=client, data={"admitted": True}, last_update_success=True)
    entry = SimpleNamespace(domain="flexdisplay", state=services.ConfigEntryState.LOADED,
                            runtime_data=SimpleNamespace(top52810=tag))
    device = SimpleNamespace(disabled_by=None, config_entries={"entry"},
        identifiers={("flexdisplay", f"entry_top52810_{address}")})
    registry = SimpleNamespace(async_get=lambda device_id: device if device_id == "tag" else None)
    monkeypatch.setattr(services.dr, "async_get", lambda hass: registry, raising=False)
    registered = {}
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: entry),
        async_add_executor_job=AsyncMock(return_value=b"png"),
        services=SimpleNamespace(has_service=lambda *args: False,
            async_register=lambda domain, name, handler, **kw: registered.update({name: (handler, kw)})))
    services.register_image_services(hass, lambda *args: pytest.fail("Must not default to a Bridge"))
    handler, config = registered["send_image"]
    call = SimpleNamespace(data=config["schema"]({"device_id": "tag", "image_file": "/media/dog.png"}))
    return services, hass, client, device, entry, handler, config, call


def test_friendly_send_converts_and_binds_hash_once(friendly):
    _, hass, client, _, _, handler, config, call = friendly
    client.send_top52810_image.assert_not_called()
    assert config["supports_response"] == "optional"
    assert asyncio.run(handler(call)) == {"job_id": "queued"}
    hass.async_add_executor_job.assert_awaited_once()
    client.prepare_top52810_image.assert_awaited_once_with(b"png", "fit")
    client.send_top52810_image.assert_awaited_once_with("AAAA", "a" * 64)
    for data in ({}, {"device_id": "tag"},
                 {**call.data, "resize_mode": "stretch"}, {**call.data, "image_base64": "AAAA"}):
        with pytest.raises(vol.Invalid):
            config["schema"](data)


def test_friendly_crop_and_picker_metadata(friendly):
    import yaml

    _, _, client, _, _, handler, config, call = friendly
    call.data = config["schema"]({**call.data, "resize_mode": "crop"})
    asyncio.run(handler(call))
    client.prepare_top52810_image.assert_awaited_once_with(b"png", "crop")
    path = Path(__file__).parents[2] / "custom_components/flexdisplay/services.yaml"
    action = yaml.safe_load(path.read_text())["send_image"]
    assert action["name"] == "Send image to display"
    assert action["fields"]["device_id"]["selector"]["device"]["filter"] == [{
        "integration": "flexdisplay", "model": "TOP52810M-D01 / MS136F6 V1.0"}]
    assert "expected_plan_sha256" not in action["fields"]
    assert action["fields"]["image_file"]["required"] is True


@pytest.mark.parametrize("failure", ["unknown", "disabled", "wrong_identity", "unloaded",
                                    "unadmitted", "unavailable", "ambiguous", "not_loaded"])
def test_friendly_selection_fails_before_read_or_send(friendly, failure):
    services, hass, client, device, entry, handler, _, call = friendly
    if failure == "unknown":
        call.data["device_id"] = "other"
    elif failure == "disabled":
        device.disabled_by = "user"
    elif failure == "wrong_identity":
        device.identifiers = set()
    elif failure == "unloaded":
        entry.runtime_data = None
    elif failure == "unadmitted":
        entry.runtime_data.top52810.data = {}
    elif failure == "unavailable":
        entry.runtime_data.top52810.last_update_success = False
    elif failure == "not_loaded":
        entry.state = "not_loaded"
    else:
        device.config_entries.add("second")
        device.identifiers.add(("flexdisplay", "second_top52810_DF:84:6B:DE:F6:ED"))
    with pytest.raises(services.ServiceValidationError):
        asyncio.run(handler(call))
    hass.async_add_executor_job.assert_not_called()
    client.send_top52810_image.assert_not_called()


@pytest.mark.parametrize("failure", ["read", "prepare", "invalid_plan", "disabled_during_prepare", "send"])
def test_friendly_failure_does_not_retry(friendly, failure):
    services, hass, client, device, _, handler, _, call = friendly
    if failure == "read":
        hass.async_add_executor_job.side_effect = OSError("read failed")
    elif failure == "prepare":
        client.prepare_top52810_image.side_effect = ValueError("bad image")
    elif failure == "invalid_plan":
        client.prepare_top52810_image.return_value["device_io"] = True
    elif failure == "disabled_during_prepare":
        async def prepare(*args):
            device.disabled_by = "user"
            return client.prepare_top52810_image.return_value
        client.prepare_top52810_image.side_effect = prepare
    else:
        client.send_top52810_image.side_effect = services.FlexDisplayApiError("busy")
    with pytest.raises(services.ServiceValidationError):
        asyncio.run(handler(call))
    assert client.send_top52810_image.await_count == (1 if failure == "send" else 0)
