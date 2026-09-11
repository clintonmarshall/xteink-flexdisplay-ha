from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import BridgeConfig
from flexdisplay_bridge.top52810_jobs import Top52810JobStore
from flexdisplay_bridge.top52810_codec import PIXEL_COUNT, PixelColor, build_transfer_plan, encode_pixels


KEY = "top52810-test-key"
ADDRESS = "DF:84:6B:DE:F6:ED"
NAME = "TRSEPD_F6ED"


def _headers() -> dict[str, str]:
    return {"X-FlexDisplay-Bridge-Key": KEY}


def _load_transport() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "custom_components"
        / "flexdisplay"
        / "top52810_transport.py"
    )
    spec = importlib.util.spec_from_file_location("top52810_transport_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TRANSPORT = _load_transport()


def test_device_summary_persists_and_duplicate_button_send_is_atomic(tmp_path):
    settings = BridgeConfig(state_path=tmp_path / "state.json", api_key=KEY)
    app = create_app(settings)
    with TestClient(app) as client:
        url = "/api/v1/stock-ble/top52810/devices"
        assert client.get(url).status_code in {401, 403}
        assert client.get(url, headers=_headers()).json() == {"devices": []}
        plan = client.post("/api/v1/stock-ble/top52810/plans/preview", headers=_headers(),
                           json={"pattern": "diagnostic", "sid": "A1B2C3"}).json()
        payload = {"pattern": "diagnostic", "sid": "A1B2C3", "address": ADDRESS,
                   "expected_name": NAME, "expected_plan_sha256": plan["plan_sha256"],
                   "reject_if_active": True}
        job = client.post("/api/v1/stock-ble/top52810/jobs", headers=_headers(), json=payload).json()
        for phase in ("waiting_for_window", "transferring", "refresh_started"):
            if phase == "transferring":
                claimed = app.state.top52810_jobs.claim(job["job_id"], "test")
            if phase == "refresh_started":
                app.state.top52810_jobs.report(job["job_id"], claimed["lease"], phase)
            assert client.post("/api/v1/stock-ble/top52810/jobs", headers=_headers(), json=payload).status_code == 400
            records = client.get(url, headers=_headers()).json()["devices"]
            assert len(records) == 1
            assert records[0]["status"] == phase
            assert records[0]["active"] is True
            assert "frames" not in records[0] and "lease" not in records[0]
        app.state.top52810_jobs.report(job["job_id"], claimed["lease"], "physically_unverified")
        record = client.get(url, headers=_headers()).json()["devices"][0]
        assert record["last_refresh_ack"]
        assert record["active"] is False
        # A subsequent queued job must not erase the previous refresh timestamp.
        assert client.post("/api/v1/stock-ble/top52810/jobs", headers=_headers(), json=payload).status_code == 200
        assert client.get(url, headers=_headers()).json()["devices"][0]["last_refresh_ack"] == record["last_refresh_ack"]
    store = Top52810JobStore(settings.state_path.with_name("flexdisplay-top52810-jobs.json"))
    assert store.devices()[0]["address"] == ADDRESS
    assert store.devices()[0]["last_refresh_ack"] == record["last_refresh_ack"]


def test_preview_is_read_only_and_queue_requires_exact_hash(tmp_path) -> None:
    app = create_app(BridgeConfig(state_path=tmp_path / "state.json", api_key=KEY))
    with TestClient(app) as client:
        preview = client.post(
            "/api/v1/stock-ble/top52810/plans/preview",
            headers=_headers(),
            json={"pattern": "diagnostic", "sid": "A1B2C3"},
        )
        assert preview.status_code == 200
        plan = preview.json()
        assert plan["device_io"] is False
        assert plan["write_count"] == 44
        assert plan["plan_sha256"] == (
            "9da514d391bfd40e444138f87d7aa8b06445633b2c37a22fa6a5969d11707876"
        )
        assert app.state.top52810_jobs.pending(ADDRESS) is None

        rejected = client.post(
            "/api/v1/stock-ble/top52810/jobs",
            headers=_headers(),
            json={
                "pattern": "diagnostic",
                "sid": "A1B2C3",
                "address": ADDRESS,
                "expected_name": NAME,
                "expected_plan_sha256": "0" * 64,
            },
        )
        assert rejected.status_code == 409
        assert app.state.top52810_jobs.pending(ADDRESS) is None

        queued = client.post(
            "/api/v1/stock-ble/top52810/jobs",
            headers=_headers(),
            json={
                "pattern": "diagnostic",
                "sid": "A1B2C3",
                "address": ADDRESS,
                "expected_name": NAME,
                "expected_plan_sha256": plan["plan_sha256"],
                "expires_seconds": 300,
            },
        )
        assert queued.status_code == 200
        assert queued.json()["status"] == "waiting_for_window"
        assert "frames" not in queued.json()


def test_claim_is_single_use_and_terminal_state_is_physically_unverified(tmp_path) -> None:
    app = create_app(BridgeConfig(state_path=tmp_path / "state.json", api_key=KEY))
    with TestClient(app) as client:
        plan = client.post(
            "/api/v1/stock-ble/top52810/plans/preview",
            headers=_headers(),
            json={"pattern": "red", "sid": "A1B2C3"},
        ).json()
        job = client.post(
            "/api/v1/stock-ble/top52810/jobs",
            headers=_headers(),
            json={
                "pattern": "red",
                "sid": "A1B2C3",
                "address": ADDRESS,
                "expected_name": NAME,
                "expected_plan_sha256": plan["plan_sha256"],
            },
        ).json()
        claimed = client.post(
            f"/api/v1/stock-ble/top52810/jobs/{job['job_id']}/claim",
            headers=_headers(),
            json={"executor_id": "ha:test"},
        )
        assert claimed.status_code == 200
        assert len(claimed.json()["frames"]) == 44
        duplicate = client.post(
            f"/api/v1/stock-ble/top52810/jobs/{job['job_id']}/claim",
            headers=_headers(),
            json={"executor_id": "ha:other"},
        )
        assert duplicate.status_code == 409
        reported = client.post(
            f"/api/v1/stock-ble/top52810/jobs/{job['job_id']}/report",
            headers=_headers(),
            json={
                "lease": claimed.json()["lease"],
                "status": "refresh_started",
                "detail": "exact refresh acknowledgement received",
            },
        )
        assert reported.status_code == 200
        assert reported.json()["status"] == "refresh_started"
        reported = client.post(
            f"/api/v1/stock-ble/top52810/jobs/{job['job_id']}/report",
            headers=_headers(),
            json={
                "lease": claimed.json()["lease"],
                "status": "physically_unverified",
                "detail": "refresh acknowledgement received",
            },
        )
        assert reported.status_code == 200
        assert reported.json()["status"] == "physically_unverified"
        assert [item["status"] for item in reported.json()["status_history"]] == [
            "queued",
            "waiting_for_window",
            "transferring",
            "refresh_started",
            "physically_unverified",
        ]


def _record(sequence: int, phase: str, payload: bytes, expected: bytes | None = None) -> dict:
    result = {
        "sequence": sequence,
        "phase": phase,
        "frame_length": len(payload),
        "frame_hex": payload.hex(" ").upper(),
        "frame_sha256": hashlib.sha256(payload).hexdigest(),
        "write_type": "with_response",
    }
    if expected is not None:
        result["expected_notify_hex"] = expected.hex(" ").upper()
    return result


def _transport_job() -> dict:
    encoded = encode_pixels((PixelColor.WHITE,) * PIXEL_COUNT)
    plan = build_transfer_plan(0xA1B2C3, encoded.black_wire, encoded.red_wire)
    return {
        "write_count": 44,
        "plan_sha256": plan.sha256,
        "sid": "A1B2C3",
        "write_uuid": "write",
        "notify_uuid": "notify",
        "frames": [frame.as_record() for frame in plan.frames],
    }


class _Client:
    def __init__(self, job: dict, failure: bool = False) -> None:
        self.job = job
        self.failure = failure
        self.callback = None
        self.writes: list[tuple[str, bytes, bool]] = []

    async def start_notify(self, _uuid, callback):
        self.callback = callback

    async def stop_notify(self, _uuid):
        self.callback = None

    async def write_gatt_char(self, uuid, payload, *, response):
        self.writes.append((uuid, payload, response))
        frame = self.job["frames"][len(self.writes) - 1]
        if frame.get("expected_notify_hex"):
            observed = bytes.fromhex(frame["expected_notify_hex"])
            if self.failure:
                observed = TRANSPORT.STATUS_FAILURE
            self.callback(None, bytearray(observed))


def test_transport_executes_exactly_once_and_waits_for_control_acks() -> None:
    job = _transport_job()
    client = _Client(job)
    asyncio.run(TRANSPORT.execute_claimed_job(client, job, notification_timeout=0.1))
    assert len(client.writes) == 44
    assert all(response for _uuid, _payload, response in client.writes)


def test_transport_stops_on_stock_failure_without_retry() -> None:
    job = _transport_job()
    client = _Client(job, failure=True)
    with pytest.raises(TRANSPORT.Top52810TransportError, match="reported failure"):
        asyncio.run(TRANSPORT.execute_claimed_job(client, job, notification_timeout=0.1))
    assert len(client.writes) == 1


def test_transport_rejects_tampered_plan_before_first_write() -> None:
    job = _transport_job()
    job["frames"][10]["frame_hex"] = "30 30 03"
    client = _Client(job)
    with pytest.raises(TRANSPORT.Top52810TransportError, match="frame 11"):
        asyncio.run(TRANSPORT.execute_claimed_job(client, job))
    assert client.writes == []


def test_session_busy_stops_before_image_data() -> None:
    job = _transport_job()

    class BusyClient(_Client):
        async def write_gatt_char(self, uuid, payload, *, response):
            self.writes.append((uuid, payload, response))
            self.callback(None, bytearray.fromhex("30 35"))

    client = BusyClient(job)
    with pytest.raises(TRANSPORT.Top52810TransportError, match="tag busy.*no image data sent; no retry"):
        asyncio.run(TRANSPORT.execute_claimed_job(client, job))
    assert len(client.writes) == 1
    assert client.callback is None


@pytest.mark.parametrize("observed", [b"", bytes.fromhex("34 31"), bytes(range(64))])
def test_unexpected_notification_has_bounded_evidence_and_stops(observed) -> None:
    job = _transport_job()

    class UnexpectedClient(_Client):
        async def write_gatt_char(self, uuid, payload, *, response):
            self.writes.append((uuid, payload, response))
            self.callback(None, bytearray(observed))

    client = UnexpectedClient(job)
    with pytest.raises(TRANSPORT.Top52810TransportError) as error:
        asyncio.run(TRANSPORT.execute_claimed_job(client, job))
    assert str(error.value) == (
        "frame 1 returned an unexpected notification; expected=30 34 00 00 00 00; "
        f"received={observed[:8].hex(' ') or '<empty>'}; "
        f"received_length={len(observed)}; truncated={len(observed) > 8}"
    )
    assert len(client.writes) == 1
    assert client.callback is None


@pytest.mark.parametrize("services", [[], ["00000200-1212-efde-1523-785fef13d123"]])
def test_advertisement_requires_complete_identity_tuple(services) -> None:
    job = {
        "address": ADDRESS,
        "expected_name": NAME,
        "manufacturer_id": 0x1A28,
        "manufacturer_payload_hex": "ffffff00000d",
        "service_uuid": "00000200-1212-efde-1523-785fef13d123",
    }
    info = SimpleNamespace(
        address=ADDRESS,
        name=NAME,
        manufacturer_data={0x1A28: bytes.fromhex("ffffff00000d")},
        service_uuids=services,
    )
    TRANSPORT.validate_advertisement(job, info)
    info.name = "TRSEPD_BEEF"
    with pytest.raises(TRANSPORT.Top52810TransportError, match="name"):
        TRANSPORT.validate_advertisement(job, info)


def test_store_restart_fails_an_interrupted_transfer(tmp_path) -> None:
    path = tmp_path / "jobs.json"
    store = Top52810JobStore(path)
    frames = [_record(index, phase, bytes((index,))) for index, phase in enumerate(TRANSPORT.EXPECTED_PHASES, 1)]
    job = store.queue(
        address=ADDRESS,
        expected_name=NAME,
        manufacturer_id=0x1A28,
        manufacturer_payload_hex="ffffff00000d",
        service_uuid="service",
        write_uuid="write",
        notify_uuid="notify",
        rendered_sha256="a" * 64,
        plan_sha256="b" * 64,
        sid="A1B2C3",
        frames=frames,
    )
    store.claim(job["job_id"], "ha:test")
    restarted = Top52810JobStore(path)
    assert restarted.get(job["job_id"])["status"] == "failed"
    assert restarted.pending(ADDRESS) is None
