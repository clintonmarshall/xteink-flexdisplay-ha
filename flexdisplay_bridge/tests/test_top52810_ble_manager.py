"""Exercise the HA manager with simulated Bluetooth; never access a radio."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def manager_module(monkeypatch):
    root = Path(__file__).parents[2] / "custom_components" / "flexdisplay"
    package = ModuleType("top52810_manager_test")
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    api = ModuleType(f"{package.__name__}.api")
    api.FlexDisplayApiClient = object
    api.FlexDisplayApiError = type("FlexDisplayApiError", (Exception,), {})
    monkeypatch.setitem(sys.modules, api.__name__, api)
    bluetooth = ModuleType("homeassistant.components.bluetooth")
    bluetooth.BluetoothCallbackMatcher = dict
    bluetooth.BluetoothCallbackReplay = SimpleNamespace(NEWEST_FIRST="newest_first")
    bluetooth.BluetoothScanningMode = SimpleNamespace(ACTIVE="active")
    bluetooth.async_register_callback = Mock(return_value=Mock())
    bluetooth.async_ble_device_from_address = Mock(return_value=object())
    components = ModuleType("homeassistant.components")
    components.bluetooth = bluetooth
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda fn: fn
    bleak = ModuleType("bleak")
    bleak.BleakClient = object
    connector = ModuleType("bleak_retry_connector")
    connector.establish_connection = AsyncMock()
    for name, module in {
        "homeassistant": ModuleType("homeassistant"),
        "homeassistant.components": components,
        "homeassistant.components.bluetooth": bluetooth,
        "homeassistant.core": core,
        "bleak": bleak,
        "bleak_retry_connector": connector,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    # Load both modules explicitly so monkeypatch also cleans up the relative import.
    for filename in ("top52810_transport", "top52810_ble"):
        name = f"{package.__name__}.{filename}"
        spec = importlib.util.spec_from_file_location(name, root / f"{filename}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    module.execute_claimed_job = AsyncMock()
    return module


def test_discovery_does_not_require_advertised_service(manager_module):
    manager = manager_module.Top52810BleManager(object(), object(), "test")
    manager.start()
    args, kwargs = manager_module.bluetooth.async_register_callback.call_args
    assert args[2] == {"manufacturer_id": 0x1A28, "connectable": True}
    assert args[3] == "active"
    assert kwargs == {"replay": "newest_first"}
    unregister = manager_module.bluetooth.async_register_callback.return_value
    manager.stop()
    unregister.assert_called_once_with()


@pytest.mark.parametrize("fault", [
    None, "address", "name", "manufacturer_id", "manufacturer_payload",
    "no_job", "not_connectable", "mtu", "service", "write", "notify",
])
def test_no_uuid_advertisement_preserves_prewrite_guards(manager_module, fault):
    module = manager_module
    job = {
        "job_id": "top52810-00000001",
        "address": "DF:84:6B:DE:F6:ED",
        "expected_name": "TRSEPD_F6ED",
        "manufacturer_id": 0x1A28,
        "manufacturer_payload_hex": "ffffff00000d",
        "service_uuid": "00000200-1212-efde-1523-785fef13d123",
        "write_uuid": "00000205-1212-efde-1523-785fef13d123",
        "notify_uuid": "00000204-1212-efde-1523-785fef13d123",
        "lease": "test-only-lease",
    }
    info = SimpleNamespace(
        address=job["address"], name=job["expected_name"],
        manufacturer_data={0x1A28: bytes.fromhex("ffffff00000d")},
        service_uuids=[],
    )
    if fault == "address":
        info.address = "DF:84:6B:DE:F6:EE"
    elif fault == "name":
        info.name = "TRSEPD_BEEF"
    elif fault == "manufacturer_id":
        info.manufacturer_data = {0xFFFF: bytes.fromhex("ffffff00000d")}
    elif fault == "manufacturer_payload":
        info.manufacturer_data = {0x1A28: bytes.fromhex("ffffff00000e")}
    api = SimpleNamespace(
        pending_top52810_job=AsyncMock(return_value=None if fault == "no_job" else job),
        claim_top52810_job=AsyncMock(return_value=job),
        report_top52810_job=AsyncMock(),
    )
    characteristics = {
        job["write_uuid"]: SimpleNamespace(properties=[] if fault == "write" else ["write"]),
        job["notify_uuid"]: SimpleNamespace(properties=[] if fault == "notify" else ["notify"]),
    }
    service = SimpleNamespace(get_characteristic=Mock(side_effect=characteristics.get))
    client = SimpleNamespace(
        mtu_size=23 if fault == "mtu" else 247,
        services=SimpleNamespace(get_service=Mock(return_value=None if fault == "service" else service)),
        disconnect=AsyncMock(),
    )
    module.establish_connection.return_value = client
    if fault == "not_connectable":
        module.bluetooth.async_ble_device_from_address.return_value = None
    manager = module.Top52810BleManager(object(), api, "test")
    asyncio.run(manager._handle_window(job["address"], info))
    if fault in {"address", "name", "manufacturer_id", "manufacturer_payload", "no_job"}:
        api.claim_top52810_job.assert_not_awaited()
        module.establish_connection.assert_not_awaited()
        api.report_top52810_job.assert_not_awaited()
    elif fault == "not_connectable":
        module.establish_connection.assert_not_awaited()
    else:
        module.establish_connection.assert_awaited_once()
        assert module.establish_connection.call_args.kwargs["max_attempts"] == 1
        client.disconnect.assert_awaited_once()
    if fault is None:
        client.services.get_service.assert_called_once_with(job["service_uuid"])
        assert service.get_characteristic.call_count == 2
        module.execute_claimed_job.assert_awaited_once_with(client, job)
        assert [call.kwargs["status"] for call in api.report_top52810_job.call_args_list] == [
            "refresh_started", "physically_unverified",
        ]
    else:
        module.execute_claimed_job.assert_not_awaited()
        if api.claim_top52810_job.await_count:
            assert api.report_top52810_job.call_args.kwargs["status"] == "failed"
    assert manager._active_addresses == set()
