"""Pure TOP52810 transfer validation and execution helpers."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from typing import Any, Protocol


ATT_VALUE_MAX = 244
STATUS_FAILURE = bytes.fromhex("30 31")
EXPECTED_PHASES = (
    "session",
    "prepare_black",
    *("black_data" for _ in range(20)),
    "prepare_red",
    *("red_data" for _ in range(20)),
    "refresh",
)


class Top52810TransportError(RuntimeError):
    """Raised when an exact stock-firmware transport invariant fails."""


class BleClient(Protocol):
    async def start_notify(self, characteristic: str, callback: Any) -> None: ...
    async def stop_notify(self, characteristic: str) -> None: ...
    async def write_gatt_char(
        self, characteristic: str, data: bytes, *, response: bool
    ) -> None: ...


def validate_advertisement(job: dict[str, Any], service_info: Any) -> None:
    """Cross-check the controller-scoped address and complete identity tuple."""
    address = str(getattr(service_info, "address", "") or "").upper()
    name = str(getattr(service_info, "name", "") or "").upper()
    if address != str(job.get("address") or "").upper():
        raise Top52810TransportError("advertisement address does not match the queued target")
    if name != str(job.get("expected_name") or "").upper():
        raise Top52810TransportError("advertisement name does not match the queued target")
    manufacturer = getattr(service_info, "manufacturer_data", {}) or {}
    observed = bytes(manufacturer.get(int(job.get("manufacturer_id") or -1), b""))
    if observed.hex() != str(job.get("manufacturer_payload_hex") or ""):
        raise Top52810TransportError("manufacturer data does not match the queued target")
    services = {str(value).lower() for value in (getattr(service_info, "service_uuids", ()) or ())}
    if str(job.get("service_uuid") or "").lower() not in services:
        raise Top52810TransportError("required service UUID was not advertised")


def validate_claimed_job(job: dict[str, Any]) -> list[tuple[bytes, bytes | None]]:
    """Fail closed before the first write if any immutable frame is malformed."""
    if int(job.get("write_count") or 0) != 44:
        raise Top52810TransportError("job does not declare exactly 44 writes")
    frames = job.get("frames")
    if not isinstance(frames, list) or len(frames) != 44:
        raise Top52810TransportError("job does not contain exactly 44 frames")
    parsed: list[tuple[bytes, bytes | None]] = []
    for index, (frame, phase) in enumerate(zip(frames, EXPECTED_PHASES, strict=True), 1):
        if not isinstance(frame, dict) or frame.get("sequence") != index or frame.get("phase") != phase:
            raise Top52810TransportError(f"frame {index} sequence or phase is invalid")
        try:
            payload = bytes.fromhex(str(frame["frame_hex"]))
        except (KeyError, TypeError, ValueError) as err:
            raise Top52810TransportError(f"frame {index} payload is invalid") from err
        if not payload or len(payload) > ATT_VALUE_MAX or frame.get("frame_length") != len(payload):
            raise Top52810TransportError(f"frame {index} length is invalid")
        if frame.get("write_type") != "with_response":
            raise Top52810TransportError(f"frame {index} is not write-with-response")
        if hashlib.sha256(payload).hexdigest() != frame.get("frame_sha256"):
            raise Top52810TransportError(f"frame {index} hash mismatch")
        expected_hex = frame.get("expected_notify_hex")
        try:
            expected = bytes.fromhex(str(expected_hex)) if expected_hex else None
        except ValueError as err:
            raise Top52810TransportError(
                f"frame {index} expected notification is invalid"
            ) from err
        parsed.append((payload, expected))
    plan_sha256 = str(job.get("plan_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", plan_sha256):
        raise Top52810TransportError("job plan hash is invalid")
    try:
        sid = str(job["sid"])
        sid_value = int(sid, 16)
    except (KeyError, TypeError, ValueError) as err:
        raise Top52810TransportError("job SID is invalid") from err
    if not re.fullmatch(r"[0-9A-F]{6}", sid):
        raise Top52810TransportError("job SID is invalid")
    expected_control = {
        1: (b"03" + bytes((7,)) + sid_value.to_bytes(4, "little"), bytes.fromhex("30 34 00 00 00 00")),
        2: (b"10\x03", bytes.fromhex("31 31")),
        23: (b"20\x03", bytes.fromhex("32 31")),
        44: (b"40\x03", bytes.fromhex("34 31")),
    }
    for sequence, (payload, expected) in enumerate(parsed, 1):
        if sequence in expected_control:
            if (payload, expected) != expected_control[sequence]:
                raise Top52810TransportError(f"frame {sequence} control command is invalid")
            continue
        expected_length = 160 if sequence in {22, 43} else ATT_VALUE_MAX
        if len(payload) != expected_length or payload[:2] != b"30" or payload[2] != len(payload):
            raise Top52810TransportError(f"frame {sequence} data command is invalid")
        if expected is not None:
            raise Top52810TransportError(f"frame {sequence} has an unexpected acknowledgement")
    plan_document = {
        "mode": "offline_prepare_only",
        "device_io": False,
        "geometry": {"width": 128, "height": 296, "bytes_per_row": 16},
        "sid": sid,
        "att_value_max": 244,
        "data_payload_max": 241,
        "plane_bytes": 4736,
        "black_data_frame_count": 20,
        "red_data_frame_count": 20,
        "total_write_count": 44,
        "status_failure_hex": "30 31",
        "frames": frames,
    }
    calculated = hashlib.sha256(
        (json.dumps(plan_document, indent=2, sort_keys=True) + "\n").encode()
    ).hexdigest()
    if not hmac.compare_digest(calculated, plan_sha256):
        raise Top52810TransportError("complete transfer-plan hash mismatch")
    return parsed


async def execute_claimed_job(
    client: BleClient,
    job: dict[str, Any],
    *,
    notification_timeout: float = 10.0,
) -> None:
    """Execute once; the caller owns connection and terminal reporting."""
    frames = validate_claimed_job(job)
    write_uuid = str(job["write_uuid"])
    notify_uuid = str(job["notify_uuid"])
    notifications: asyncio.Queue[bytes] = asyncio.Queue()

    def notification(_sender: Any, data: bytearray) -> None:
        notifications.put_nowait(bytes(data))

    await client.start_notify(notify_uuid, notification)
    try:
        for index, (payload, expected) in enumerate(frames, 1):
            await client.write_gatt_char(write_uuid, payload, response=True)
            if expected is None:
                continue
            while True:
                try:
                    observed = await asyncio.wait_for(
                        notifications.get(), timeout=notification_timeout
                    )
                except TimeoutError as err:
                    raise Top52810TransportError(
                        f"frame {index} acknowledgement timed out"
                    ) from err
                if observed == STATUS_FAILURE:
                    raise Top52810TransportError(
                        f"stock firmware reported failure after frame {index}"
                    )
                if observed == expected:
                    break
                raise Top52810TransportError(
                    f"frame {index} returned an unexpected notification"
                )
    finally:
        await client.stop_notify(notify_uuid)
