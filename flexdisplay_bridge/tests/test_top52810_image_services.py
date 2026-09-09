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
    assert len(registered) == 2
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
