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
    bluetooth.async_last_service_info = Mock(return_value=None)
    bluetooth.async_discovered_service_info = Mock(return_value=[])
    event = ModuleType("homeassistant.helpers.event")
    event.async_track_time_interval = Mock(return_value=Mock())
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
        "homeassistant.helpers": ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.event": event,
        "bleak": bleak,
        "bleak_retry_connector": connector,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    # Load both modules explicitly so monkeypatch also cleans up the relative import.
    for filename in ("top52810_timing", "top52810_transport", "top52810_ble"):
        name = f"{package.__name__}.{filename}"
        spec = importlib.util.spec_from_file_location(name, root / f"{filename}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    module.execute_claimed_job = AsyncMock()
    module.async_sleep = AsyncMock()
    module.monotonic = Mock(return_value=100.0)
    return module


def test_discovery_does_not_require_advertised_service(manager_module):
    manager = manager_module.Top52810BleManager(object(), object(), "test")
    manager.start()
    args, kwargs = manager_module.bluetooth.async_register_callback.call_args
    assert args[2] == {"manufacturer_id": 0x1A28, "connectable": True}
    assert args[3] == "active"
    assert kwargs == {"replay": "newest_first"}
    unregister = manager_module.bluetooth.async_register_callback.return_value
    cancel_timer = manager_module.async_track_time_interval.return_value
    assert manager_module.async_track_time_interval.call_args.args[2].total_seconds() == 5
    manager.stop()
    unregister.assert_called_once_with()
    cancel_timer.assert_called_once_with()


@pytest.mark.parametrize("fault", [
    None, "address", "name", "manufacturer_id", "manufacturer_payload",
    "no_job", "not_connectable", "mtu", "service", "write", "notify",
    "transport", "settle_cancel",
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
        time=100.0,
    )
    module.bluetooth.async_last_service_info.return_value = info
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
    async def settle(seconds):
        assert seconds == 20.0
        module.execute_claimed_job.assert_awaited_once_with(client, job)
        client.disconnect.assert_not_awaited()
        if fault == "settle_cancel":
            raise asyncio.CancelledError()

    module.async_sleep.side_effect = settle
    if fault == "transport":
        module.execute_claimed_job.side_effect = module.Top52810TransportError("tag busy")
    if fault == "settle_cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(manager._handle_window(job["address"], info))
        client.disconnect.assert_awaited_once()
        module.async_sleep.assert_awaited_once_with(20.0)
        module.establish_connection.assert_awaited_once()
        assert manager._active_addresses == set()
        return
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
        module.async_sleep.assert_awaited_once_with(20.0)
        client.services.get_service.assert_called_once_with(job["service_uuid"])
        assert service.get_characteristic.call_count == 2
        module.execute_claimed_job.assert_awaited_once_with(client, job)
        assert [call.kwargs["status"] for call in api.report_top52810_job.call_args_list] == [
            "refresh_started", "physically_unverified",
        ]
    else:
        module.async_sleep.assert_not_awaited()
        if fault == "transport":
            module.execute_claimed_job.assert_awaited_once_with(client, job)
        else:
            module.execute_claimed_job.assert_not_awaited()
        if api.claim_top52810_job.await_count:
            assert api.report_top52810_job.call_args.kwargs["status"] == "failed"
    assert manager._active_addresses == set()


@pytest.mark.parametrize("fault", [None, "cached", "boundary", "stale", "missing", "future", "nan", "stopped", "expired", "failed_claim", "expired_after_claim", "stopped_after_claim"])
def test_job_queued_after_discovery_without_new_callback(manager_module, fault):
    """HA suppresses duplicate callbacks; its latest observation still updates."""
    module = manager_module
    address = "DF:84:6B:DE:F6:ED"
    info = SimpleNamespace(address=address, name="TRSEPD_F6ED", time=100.0,
                           manufacturer_data={0x1A28: bytes.fromhex("ffffff00000d")})
    job = {"job_id": "top52810-00000002", "address": address,
           "expected_name": info.name, "manufacturer_id": 0x1A28,
           "manufacturer_payload_hex": "ffffff00000d", "lease": "test-only-lease",
           "service_uuid": "service", "write_uuid": "write", "notify_uuid": "notify"}
    api = SimpleNamespace(pending_top52810_job=AsyncMock(return_value=None),
                          claim_top52810_job=AsyncMock(return_value=job),
                          report_top52810_job=AsyncMock())
    client = SimpleNamespace(mtu_size=247, disconnect=AsyncMock(), services=SimpleNamespace(
        get_service=Mock(return_value=SimpleNamespace(get_characteristic=Mock(
            side_effect=lambda uuid: SimpleNamespace(properties=[uuid]))))))
    module.establish_connection.return_value = client
    module.bluetooth.async_last_service_info.return_value = info
    module.bluetooth.async_discovered_service_info.return_value = [info]

    async def run():
        tasks = []

        def create_task(coro, name):
            task = asyncio.create_task(coro, name=name)
            tasks.append(task)
            return task

        manager = module.Top52810BleManager(SimpleNamespace(async_create_task=create_task), api, "test")
        manager.start()
        # Initial discovery predates the authorized job.
        manager._advertisement(info, None)
        await asyncio.gather(*tasks)
        api.pending_top52810_job.assert_awaited_once_with(address)
        api.claim_top52810_job.assert_not_awaited()
        tasks.clear()
        api.pending_top52810_job.return_value = None if fault == "expired" else job
        if fault == "stale":
            info.time = -201.0
        elif fault == "cached":
            info.time = 57.0
        elif fault == "boundary":
            info.time = -200.0
        elif fault == "nan":
            info.time = float("nan")
        elif fault == "future":
            info.time = 101.0
        elif fault == "missing":
            module.bluetooth.async_last_service_info.return_value = None
        elif fault == "stopped":
            manager.stop()
        elif fault == "failed_claim":
            api.claim_top52810_job.side_effect = RuntimeError("already claimed or expired")
        elif fault in {"expired_after_claim", "stopped_after_claim"}:
            async def claim(*args):
                if fault == "stopped_after_claim":
                    manager.stop()
                else:
                    module.monotonic.return_value = 401.0
                return job
            api.claim_top52810_job.side_effect = claim
        # Timer checks without any new Bluetooth callback; overlap coalesces.
        manager._check_known_tags(None)
        manager._check_known_tags(None)
        manager._advertisement(info, None)
        await asyncio.gather(*tasks)
        if fault in {None, "cached", "boundary"}:
            module.execute_claimed_job.assert_awaited_once_with(client, job)
            api.claim_top52810_job.assert_awaited_once()
            # Terminal jobs disappear from the pending endpoint. No retry.
            api.pending_top52810_job.return_value = None
            manager._check_known_tags(None)
            await asyncio.gather(*tasks)
            module.execute_claimed_job.assert_awaited_once()
        else:
            module.establish_connection.assert_not_awaited()
            module.execute_claimed_job.assert_not_awaited()
            if fault in {"expired_after_claim", "stopped_after_claim"}:
                api.claim_top52810_job.assert_awaited_once()
                assert api.report_top52810_job.call_args.kwargs["status"] == "failed"
            elif fault != "failed_claim":
                api.claim_top52810_job.assert_not_awaited()
        manager.stop()
        assert not manager._active_addresses

    asyncio.run(run())


@pytest.mark.parametrize("stop", [False, True])
def test_window_stales_or_unloads_while_pending_request_in_flight(manager_module, stop):
    module = manager_module
    info = SimpleNamespace(address="DF:84:6B:DE:F6:ED", time=100.0)
    api = SimpleNamespace(pending_top52810_job=AsyncMock(), claim_top52810_job=AsyncMock())
    manager = module.Top52810BleManager(object(), api, "test")

    async def pending(address):
        if stop:
            manager.stop()
        else:
            info.time = -201.0
        return {"job_id": "top52810-00000002"}

    api.pending_top52810_job.side_effect = pending
    module.bluetooth.async_last_service_info.return_value = info
    asyncio.run(manager._handle_window(info.address, info))
    api.claim_top52810_job.assert_not_awaited()
    module.establish_connection.assert_not_awaited()
