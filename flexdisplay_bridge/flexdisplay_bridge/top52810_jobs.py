"""Durable, command-gated jobs for TOP52810 stock-firmware BLE delivery."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any


ADDRESS = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ACTIVE = frozenset({"queued", "waiting_for_window", "transferring"})
TERMINAL = frozenset({"physically_unverified", "failed", "expired", "superseded"})


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Top52810JobStore:
    """Persist one bounded stream of immutable transfer plans."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"schema_version": 1, "sequence": 0, "jobs": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(loaded, dict)
            or loaded.get("schema_version") != 1
            or not isinstance(loaded.get("jobs"), dict)
        ):
            raise ValueError("TOP52810 job state has an unsupported schema")
        self._state = loaded
        # A transfer cannot safely resume after a Bridge restart.
        changed = False
        for job in self._state["jobs"].values():
            if job.get("status") == "transferring":
                job["status"] = "failed"
                job["failure"] = "bridge_restarted_during_transfer"
                job["updated_at"] = utc_now()
                changed = True
            elif job.get("status") == "refresh_started":
                job["status"] = "physically_unverified"
                job["detail"] = "bridge restarted after refresh acknowledgement"
                job["updated_at"] = utc_now()
                job.pop("lease", None)
                changed = True
        if changed:
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        encoded = json.dumps(self._state, indent=2, sort_keys=True).encode("utf-8")
        try:
            with temporary.open("wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _public(job: dict[str, Any], *, include_frames: bool = False) -> dict[str, Any]:
        result = {
            key: deepcopy(value)
            for key, value in job.items()
            if key not in {"frames", "lease"}
        }
        if include_frames:
            result["frames"] = deepcopy(job["frames"])
            result["lease"] = str(job["lease"])
        return result

    def queue(
        self,
        *,
        address: str,
        expected_name: str,
        manufacturer_id: int,
        manufacturer_payload_hex: str,
        service_uuid: str,
        write_uuid: str,
        notify_uuid: str,
        rendered_sha256: str,
        plan_sha256: str,
        sid: str,
        frames: list[dict[str, Any]],
        expires_seconds: int = 900,
    ) -> dict[str, Any]:
        address = address.strip().upper()
        if not ADDRESS.fullmatch(address):
            raise ValueError("address must be a canonical Bluetooth MAC address")
        if not 0 <= manufacturer_id <= 0xFFFF:
            raise ValueError("manufacturer_id must fit in 16 bits")
        if not re.fullmatch(r"[0-9a-f]+", manufacturer_payload_hex) or len(manufacturer_payload_hex) % 2:
            raise ValueError("manufacturer payload must be lower-case hexadecimal")
        if not SHA256.fullmatch(rendered_sha256) or not SHA256.fullmatch(plan_sha256):
            raise ValueError("render and plan hashes must be lower-case SHA-256 values")
        if len(frames) != 44:
            raise ValueError("TOP52810 jobs must contain exactly 44 writes")
        if not 60 <= expires_seconds <= 3600:
            raise ValueError("expiry must be between 60 and 3600 seconds")
        now = datetime.now(UTC)
        with self._lock:
            self.expire(now=now)
            for existing in self._state["jobs"].values():
                if existing.get("address") == address and existing.get("status") in ACTIVE:
                    existing["status"] = "superseded"
                    existing["updated_at"] = utc_now()
            self._state["sequence"] = int(self._state.get("sequence") or 0) + 1
            job_id = f"top52810-{self._state['sequence']:08x}"
            job = {
                "job_id": job_id,
                "family": "TOP52810M-D01",
                "board": "MS136F6 V1.0",
                "address": address,
                "expected_name": expected_name,
                "manufacturer_id": manufacturer_id,
                "manufacturer_payload_hex": manufacturer_payload_hex,
                "service_uuid": service_uuid.lower(),
                "write_uuid": write_uuid.lower(),
                "notify_uuid": notify_uuid.lower(),
                "rendered_sha256": rendered_sha256,
                "plan_sha256": plan_sha256,
                "sid": sid,
                "write_count": 44,
                "status": "waiting_for_window",
                "created_at": now.isoformat(timespec="seconds"),
                "updated_at": now.isoformat(timespec="seconds"),
                "expires_at": (now + timedelta(seconds=expires_seconds)).isoformat(timespec="seconds"),
                "frames": deepcopy(frames),
                "attempt_count": 0,
                "status_history": [
                    {"status": "queued", "at": now.isoformat(timespec="seconds")},
                    {
                        "status": "waiting_for_window",
                        "at": now.isoformat(timespec="seconds"),
                    },
                ],
            }
            self._state["jobs"][job_id] = job
            self._save()
            return self._public(job)

    def expire(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        changed = False
        for job in self._state["jobs"].values():
            if job.get("status") not in ACTIVE:
                continue
            if datetime.fromisoformat(str(job["expires_at"])) <= current:
                job["status"] = "expired"
                job["updated_at"] = current.isoformat(timespec="seconds")
                changed = True
        if changed:
            self._save()

    def pending(self, address: str) -> dict[str, Any] | None:
        address = address.strip().upper()
        with self._lock:
            self.expire()
            candidates = [
                job for job in self._state["jobs"].values()
                if job.get("address") == address and job.get("status") == "waiting_for_window"
            ]
            if not candidates:
                return None
            return self._public(max(candidates, key=lambda job: job["created_at"]))

    def claim(self, job_id: str, executor_id: str) -> dict[str, Any]:
        with self._lock:
            self.expire()
            job = self._state["jobs"].get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.get("status") != "waiting_for_window":
                raise ValueError("job is no longer waiting for an advertisement window")
            lease = secrets.token_hex(16)
            job.update(
                status="transferring",
                executor_id=executor_id[:128],
                lease=lease,
                attempt_count=int(job.get("attempt_count") or 0) + 1,
                updated_at=utc_now(),
            )
            job.setdefault("status_history", []).append(
                {"status": "transferring", "at": job["updated_at"]}
            )
            self._save()
            return self._public(job, include_frames=True)

    def report(self, job_id: str, lease: str, status: str, detail: str = "") -> dict[str, Any]:
        if status not in {"refresh_started", "physically_unverified", "failed"}:
            raise ValueError(
                "report status must be refresh_started, physically_unverified or failed"
            )
        with self._lock:
            job = self._state["jobs"].get(job_id)
            if not job:
                raise KeyError(job_id)
            current_status = str(job.get("status") or "")
            allowed_current = {"transferring"}
            if status in {"physically_unverified", "failed"}:
                allowed_current.add("refresh_started")
            if current_status not in allowed_current or not secrets.compare_digest(
                str(job.get("lease") or ""), lease
            ):
                raise ValueError("job lease is invalid or no longer active")
            job["status"] = status
            job["updated_at"] = utc_now()
            job["detail"] = detail[:512]
            job.setdefault("status_history", []).append(
                {"status": status, "at": job["updated_at"], "detail": job["detail"]}
            )
            if status != "refresh_started":
                job.pop("lease", None)
            self._save()
            return self._public(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self.expire()
            job = self._state["jobs"].get(job_id)
            return self._public(job) if job else None
