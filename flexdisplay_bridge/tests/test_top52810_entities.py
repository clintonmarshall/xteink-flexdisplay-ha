"""Isolated HA entity contracts; no network, Bluetooth or device writes."""

import asyncio
from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def module(monkeypatch):
    root = Path(__file__).parents[2] / "custom_components" / "flexdisplay"
    package = ModuleType("stock_entity_test")
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, package.__name__, package)

    class Coordinator:
        def __init__(self, hass, *args, **kwargs):
            self.hass = hass
            self.data = {}
            self.last_update_success = True
            self.async_request_refresh = AsyncMock()
            self.listeners = []

        def async_add_listener(self, fn):
            self.listeners.append(fn)
            return Mock()

    class Entity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    modules = {
        "aiohttp": {"ClientError": type("ClientError", (Exception,), {}),
                    "ClientResponseError": type("ClientResponseError", (Exception,), {}),
                    "ClientSession": object},
        "homeassistant": {},
        "homeassistant.components": {},
        "homeassistant.components.bluetooth": {"async_last_service_info": Mock(return_value=None)},
        "homeassistant.components.sensor": {"SensorEntity": type("Sensor", (), {}), "SensorDeviceClass": SimpleNamespace(TIMESTAMP="timestamp")},
        "homeassistant.components.button": {"ButtonEntity": type("Button", (), {})},
        "homeassistant.exceptions": {"HomeAssistantError": type("HomeAssistantError", (Exception,), {})},
        "homeassistant.helpers": {},
        "homeassistant.helpers.device_registry": {"DeviceInfo": dict},
        "homeassistant.helpers.update_coordinator": {"DataUpdateCoordinator": Coordinator, "CoordinatorEntity": Entity, "UpdateFailed": type("UpdateFailed", (Exception,), {})},
        "homeassistant.util": {},
        "homeassistant.util.dt": {"parse_datetime": datetime.fromisoformat, "utcnow": lambda: datetime.now(UTC)},
        "stock_entity_test.const": {"DOMAIN": "flexdisplay"},
    }
    for name, attrs in modules.items():
        mod = ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
    for filename in ("api", "top52810_timing", "top52810_entity"):
        name = f"stock_entity_test.{filename}"
        spec = importlib.util.spec_from_file_location(name, root / f"{filename}.py")
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, mod)
        spec.loader.exec_module(mod)
    mod.monotonic = Mock(return_value=100)
    return mod


def record(module):
    return {"address": module.ADDRESS, "expected_name": module.NAME,
            "manufacturer_id": module.MANUFACTURER_ID,
            "manufacturer_payload_hex": module.MANUFACTURER_PAYLOAD,
            "status": "physically_unverified", "active": False}


def test_identity_sleep_freshness_and_api_failure(module):
    async def run():
        api = SimpleNamespace(top52810_devices=AsyncMock(return_value=[record(module)]))
        coordinator = module.Top52810Coordinator(object(), api, "entry")
        coordinator.data = await coordinator._async_update_data()
        sensor = module.Top52810Sensor(coordinator, "connection", "Bluetooth window")
        assert sensor.available and sensor.native_value == "waiting_for_window"
        info = SimpleNamespace(address=module.ADDRESS, name=module.NAME, time=95, manufacturer_data={module.MANUFACTURER_ID: bytes.fromhex(module.MANUFACTURER_PAYLOAD)})
        module.bluetooth.async_last_service_info.return_value = info
        coordinator.data = await coordinator._async_update_data()
        assert sensor.native_value == "advertising"
        seen = coordinator.data["last_seen"]
        module.monotonic.return_value = 120
        coordinator.data = await coordinator._async_update_data()
        assert sensor.native_value == "recently_seen"
        assert coordinator.data["last_seen"] == seen
        module.monotonic.return_value = 396
        coordinator.data = await coordinator._async_update_data()
        assert sensor.native_value == "waiting_for_window"
        assert coordinator.data["last_seen"] == seen
        coordinator.last_update_success = False
        assert not sensor.available
        api.top52810_devices.side_effect = module.FlexDisplayApiError("offline")
        with pytest.raises(module.UpdateFailed):
            await coordinator._async_update_data()
        api.top52810_devices.side_effect = None
        api.top52810_devices.return_value = [{**record(module), "address": "AA:BB:CC:DD:EE:FF"}]
        assert await coordinator._async_update_data() == {}
    asyncio.run(run())


def test_entities_added_once_and_button_fails_closed(module):
    async def run():
        api = SimpleNamespace(send_top52810_diagnostic=AsyncMock())
        coordinator = module.Top52810Coordinator(object(), api, "entry")
        entry = SimpleNamespace(runtime_data=SimpleNamespace(top52810=coordinator), async_on_unload=Mock())
        add = Mock()
        module.setup_top52810_entities(entry, add)
        add.assert_not_called()
        coordinator.data = record(module)
        coordinator.listeners[0]()
        coordinator.listeners[0]()
        add.assert_called_once()
        sensors = add.call_args.args[0]
        assert len(sensors) == 4
        assert len({s._attr_unique_id for s in sensors}) == 4
        button = module.Top52810SendButton(coordinator)
        assert button._attr_device_info == sensors[0]._attr_device_info
        coordinator.data["active"] = True
        with pytest.raises(module.HomeAssistantError):
            await button.async_press()
        api.send_top52810_diagnostic.assert_not_called()
        coordinator.data["active"] = False
        await button.async_press()
        api.send_top52810_diagnostic.assert_awaited_once_with(module.ADDRESS, module.NAME)
        assert not button._sending
        assert sensors[0].extra_state_attributes["physical_image_verified"] is False
    asyncio.run(run())


def test_api_preview_hash_and_duplicate_guards(module):
    async def run():
        api_module = sys.modules["stock_entity_test.api"]
        client = api_module.FlexDisplayApiClient(None, "http://example.invalid")
        client._request = AsyncMock(return_value={"plan_sha256": "bad"})
        with pytest.raises(module.FlexDisplayApiError):
            await client.send_top52810_diagnostic(module.ADDRESS, module.NAME)
        assert client._request.await_count == 1
        client._request = AsyncMock(side_effect=[{
            "plan_sha256": "9da514d391bfd40e444138f87d7aa8b06445633b2c37a22fa6a5969d11707876",
            "write_count": 44, "device_io": False}, {"job_id": "test"}])
        await client.send_top52810_diagnostic(module.ADDRESS, module.NAME)
        payload = client._request.call_args.kwargs["json"]
        assert payload["reject_if_active"] is True
        assert payload["expires_seconds"] == 900
        assert payload["address"] == module.ADDRESS
    asyncio.run(run())


@pytest.mark.parametrize("fault", ["address", "name", "payload", "future", "nan", "infinite"])
def test_untrusted_advertisement_does_not_report_online(module, fault):
    async def run():
        api = SimpleNamespace(top52810_devices=AsyncMock(return_value=[record(module)]))
        coordinator = module.Top52810Coordinator(object(), api, "entry")
        info = SimpleNamespace(address=module.ADDRESS, name=module.NAME, time=99,
                               manufacturer_data={module.MANUFACTURER_ID: bytes.fromhex(module.MANUFACTURER_PAYLOAD)})
        if fault == "payload":
            info.manufacturer_data = {}
        elif fault == "future":
            info.time = 101
        elif fault == "nan":
            info.time = float("nan")
        elif fault == "infinite":
            info.time = -float("inf")
        else:
            setattr(info, fault, "wrong")
        module.bluetooth.async_last_service_info.return_value = info
        data = await coordinator._async_update_data()
        assert data["connection"] == "waiting_for_window"
        assert data["last_seen"] is None
    asyncio.run(run())


@pytest.mark.parametrize("age,expected", [
    (0, "advertising"), (10, "advertising"), (10.1, "recently_seen"),
    (43, "recently_seen"), (300, "recently_seen"),
    (300.1, "waiting_for_window"), (-1, "waiting_for_window"),
])
def test_observation_age_boundaries(module, age, expected):
    api = SimpleNamespace(top52810_devices=AsyncMock(return_value=[record(module)]))
    coordinator = module.Top52810Coordinator(object(), api, "entry")
    module.bluetooth.async_last_service_info.return_value = SimpleNamespace(
        address=module.ADDRESS, name=module.NAME, time=100-age,
        manufacturer_data={module.MANUFACTURER_ID: bytes.fromhex(module.MANUFACTURER_PAYLOAD)},
    )
    data = asyncio.run(coordinator._async_update_data())
    assert data["connection"] == expected
