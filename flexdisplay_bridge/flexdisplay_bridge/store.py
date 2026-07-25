from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DeviceStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"devices": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("devices"), dict):
                self._state = loaded
                self._migrate_legacy_commands()
        except (OSError, json.JSONDecodeError):
            self._state = {"devices": {}}

    def _migrate_legacy_commands(self) -> None:
        """Make pre-command-ID state safe after upgrading the Bridge."""
        changed = False
        sequence = int(self._state.get("command_sequence", 0))
        for record in self._state["devices"].values():
            pending = list(record.get("pending_commands") or [])
            if pending and not record.get("pending_command_id"):
                if "install" in pending:
                    record["legacy_pending_commands"] = pending[-8:]
                    record["legacy_pending_install_cancelled_at"] = utc_now()
                    pending = [command for command in pending if command != "install"]
                    record["pending_commands"] = pending
                if pending:
                    sequence += 1
                    device_id = str(record.get("device_id") or "device")
                    record["pending_command_id"] = f"{device_id}-{sequence:08x}"
                changed = True

            dispatched = record.get("dispatched_commands") or []
            if dispatched and not record.get("dispatched_command_id"):
                record["legacy_dispatched_commands"] = list(dispatched)[-8:]
                record["dispatched_commands"] = []
                record["legacy_commands_cleared_at"] = utc_now()
                changed = True

        if changed:
            self._state["command_sequence"] = sequence
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._state, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def touch(self, device_id: str, telemetry: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            devices = self._state["devices"]
            record = devices.setdefault(
                device_id,
                {
                    "device_id": device_id,
                    "first_seen": utc_now(),
                    "pending_commands": [],
                    "render_revision": 0,
                },
            )
            record.update({key: value for key, value in telemetry.items() if value is not None})
            record["last_seen"] = utc_now()
            self._save()
            return deepcopy(record)

    def ensure_provisioning(self, device_id: str, assignment: dict[str, Any]) -> dict[str, Any]:
        """Persist the initial assignment for an automatically discovered device."""
        with self._lock:
            record = self._state["devices"].setdefault(
                device_id,
                {
                    "device_id": device_id,
                    "first_seen": utc_now(),
                    "pending_commands": [],
                    "render_revision": 0,
                },
            )
            changed = False
            for key, value in assignment.items():
                if key not in record and value is not None:
                    record[key] = value
                    changed = True
            if not record.get("provisioned"):
                record["provisioned"] = True
                record["provisioned_at"] = utc_now()
                changed = True
            if changed:
                self._save()
            return deepcopy(record)

    def provision(self, device_id: str, assignment: dict[str, Any]) -> dict[str, Any]:
        """Update an assignment from the authenticated bridge API."""
        with self._lock:
            record = self._state["devices"].setdefault(
                device_id,
                {
                    "device_id": device_id,
                    "first_seen": utc_now(),
                    "last_seen": None,
                    "pending_commands": [],
                    "render_revision": 0,
                },
            )
            record.update(assignment)
            record["provisioned"] = True
            record["provisioned_at"] = record.get("provisioned_at") or utc_now()
            record["provisioning_updated_at"] = utc_now()
            record["render_revision"] = int(record.get("render_revision", 0)) + 1
            self._save()
            return deepcopy(record)

    def queue_command(self, device_id: str, command: str) -> dict[str, Any]:
        with self._lock:
            record = self._state["devices"].setdefault(
                device_id,
                {
                    "device_id": device_id,
                    "first_seen": utc_now(),
                    "last_seen": None,
                    "pending_commands": [],
                    "render_revision": 0,
                },
            )
            pending = record.setdefault("pending_commands", [])
            if not pending:
                sequence = int(self._state.get("command_sequence", 0)) + 1
                self._state["command_sequence"] = sequence
                record["pending_command_id"] = f"{device_id}-{sequence:08x}"
            if command not in pending:
                pending.append(command)
            record["command_queued_at"] = utc_now()
            self._save()
            return deepcopy(record)

    def consume_commands(self, device_id: str) -> list[str]:
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return []
            commands = list(record.get("pending_commands", []))
            record["pending_commands"] = []
            if commands:
                record["dispatched_commands"] = commands
                record["dispatched_command_id"] = record.pop("pending_command_id", None)
                record["command_dispatched_at"] = utc_now()
                record["render_revision"] = int(record.get("render_revision", 0)) + 1
                self._save()
            return commands

    def clear_commands(self, device_id: str) -> dict[str, Any] | None:
        """Cancel commands that have not yet been delivered to a device."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            record["pending_commands"] = []
            record.pop("pending_command_id", None)
            record["commands_cancelled_at"] = utc_now()
            self._save()
            return deepcopy(record)

    def acknowledge(self, device_id: str, result: str, command_id: str = "") -> bool:
        if not result:
            return False
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return False
            dispatched_id = str(record.get("dispatched_command_id") or "")
            if command_id:
                if (
                    not dispatched_id
                    and command_id == record.get("last_command_id")
                    and result[:160] == record.get("last_command_result")
                ):
                    return True
                if not dispatched_id or command_id != dispatched_id:
                    record["last_stale_command_result"] = result[:160]
                    record["last_stale_command_id"] = command_id[:96]
                    record["stale_command_result_at"] = utc_now()
                    self._save()
                    return False
            record["last_command_result"] = result[:160]
            record["last_command_id"] = (command_id or dispatched_id)[:96]
            record["command_completed_at"] = utc_now()
            record["dispatched_commands"] = []
            record.pop("dispatched_command_id", None)
            history = record.setdefault("command_history", [])
            history.append(
                {
                    "command_id": record["last_command_id"],
                    "result": record["last_command_result"],
                    "completed_at": record["command_completed_at"],
                }
            )
            record["command_history"] = history[-16:]
            self._update_firmware_rollout(record, result)
            self._save()
            return True

    def firmware_rollout(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state.get("firmware_rollout") or {})

    def verify_usb_recovery(
        self,
        device_id: str,
        target_version: str,
        expected_command_id: str,
        *,
        max_checkin_age_seconds: int = 600,
    ) -> dict[str, Any]:
        """Reconcile a physically verified USB recovery without forging a device ACK."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                raise ValueError("Device has not checked in")

            rollout = self._state.get("firmware_rollout") or {}
            dispatched = list(record.get("dispatched_commands") or [])
            dispatched_id = str(record.get("dispatched_command_id") or "")
            blockers: list[str] = []
            if not target_version:
                blockers.append("A target firmware version is required")
            if rollout.get("target_version") != target_version:
                blockers.append("The active rollout does not match the requested target")
            if rollout.get("status") != "canary_active":
                blockers.append("The rollout is not waiting for an active canary")
            if rollout.get("canary_device_id") != device_id:
                blockers.append("This device is not the active canary")
            if record.get("firmware") != target_version:
                blockers.append("The device is not reporting the exact target firmware")
            if record.get("usb_connected") is not True:
                blockers.append("The device is not reporting USB power")
            if record.get("sd_ready") is not True:
                blockers.append("The device SD card is not ready")
            if record.get("pending_commands"):
                blockers.append("The device has pending commands")
            if dispatched != ["install"]:
                blockers.append("The only dispatched command must be the stuck install")
            if not expected_command_id or dispatched_id != expected_command_id:
                blockers.append("The expected command ID does not match the stuck install")

            last_seen = record.get("last_seen")
            try:
                seen = datetime.fromisoformat(str(last_seen))
                checkin_age = (datetime.now(UTC) - seen).total_seconds()
                if checkin_age < 0 or checkin_age > max_checkin_age_seconds:
                    blockers.append("The device check-in is not recent enough")
            except (TypeError, ValueError):
                blockers.append("The device has no valid recent check-in")

            if blockers:
                raise ValueError("; ".join(blockers))

            verified_at = utc_now()
            evidence = {
                "method": "usb_recovery",
                "device_id": device_id,
                "target_version": target_version,
                "observed_firmware": str(record.get("firmware") or ""),
                "observed_usb_connected": True,
                "observed_sd_ready": True,
                "observed_last_seen": last_seen,
                "reconciled_command_id": dispatched_id,
                "verified_at": verified_at,
            }

            record["dispatched_commands"] = []
            record.pop("dispatched_command_id", None)
            record["last_command_id"] = dispatched_id[:96]
            record["last_command_result"] = "install:usb-recovery-verified"
            record["command_completed_at"] = verified_at
            record["firmware_update_status"] = "verified"
            record["firmware_verification_method"] = "usb_recovery"
            record["firmware_verified_at"] = verified_at
            record["last_usb_recovery_verification"] = evidence
            history = record.setdefault("command_history", [])
            history.append(
                {
                    "command_id": dispatched_id[:96],
                    "result": "install:usb-recovery-verified",
                    "verification_method": "usb_recovery",
                    "completed_at": verified_at,
                }
            )
            record["command_history"] = history[-16:]
            recovery_history = record.setdefault("usb_recovery_history", [])
            recovery_history.append(evidence)
            record["usb_recovery_history"] = recovery_history[-8:]

            updated = rollout.setdefault("updated_devices", [])
            if device_id not in updated:
                updated.append(device_id)
            rollout["status"] = "canary_verified"
            rollout["canary_verified_at"] = verified_at
            rollout["last_verified_at"] = verified_at
            rollout["last_verified_device_id"] = device_id
            rollout["last_verification_method"] = "usb_recovery"
            rollout_history = rollout.setdefault("verification_history", [])
            rollout_history.append(evidence)
            rollout["verification_history"] = rollout_history[-16:]

            self._save()
            return deepcopy(record)

    def active_firmware_installs(self) -> int:
        with self._lock:
            return sum(
                1
                for record in self._state["devices"].values()
                if "install" in (record.get("pending_commands") or [])
                or "install" in (record.get("dispatched_commands") or [])
            )

    def queue_firmware_install(
        self,
        device_id: str,
        target_version: str,
        *,
        canary_required: bool,
        max_parallel: int,
    ) -> dict[str, Any]:
        """Queue an install while enforcing a persistent canary and concurrency gate."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                raise ValueError("Device has not checked in")

            rollout = self._state.get("firmware_rollout") or {}
            if rollout.get("target_version") != target_version:
                rollout = {
                    "target_version": target_version,
                    "status": "awaiting_canary" if canary_required else "fleet_active",
                    "started_at": utc_now(),
                    "updated_devices": [],
                }
                self._state["firmware_rollout"] = rollout

            already_active = (
                "install" in (record.get("pending_commands") or [])
                or "install" in (record.get("dispatched_commands") or [])
            )
            if already_active:
                return deepcopy(record)

            active = self.active_firmware_installs()
            if active >= max_parallel:
                raise ValueError(f"Maximum of {max_parallel} concurrent firmware install(s) reached")

            if canary_required:
                status = str(rollout.get("status") or "awaiting_canary")
                canary_id = str(rollout.get("canary_device_id") or "")
                if status == "failed":
                    raise ValueError("Firmware rollout failed; configure a new release before continuing")
                if status in {"awaiting_canary", "canary_active"}:
                    if canary_id and canary_id != device_id:
                        raise ValueError(f"Waiting for canary {canary_id} to boot and acknowledge")
                    rollout["canary_device_id"] = device_id
                    rollout["status"] = "canary_active"
                    rollout["canary_started_at"] = rollout.get("canary_started_at") or utc_now()

            queued = self.queue_command(device_id, "install")
            rollout["last_queued_device_id"] = device_id
            rollout["last_queued_at"] = utc_now()
            record = self._state["devices"][device_id]
            is_canary = rollout.get("canary_device_id") == device_id
            record["firmware_update_role"] = "canary" if is_canary else "fleet"
            record["firmware_update_target"] = target_version
            record["firmware_update_status"] = "queued"
            if not is_canary and rollout.get("status") == "canary_verified":
                rollout["status"] = "fleet_active"
            self._save()
            del queued
            return deepcopy(record)

    def _update_firmware_rollout(self, record: dict[str, Any], result: str) -> None:
        if not result.startswith("install:"):
            return
        rollout = self._state.get("firmware_rollout")
        if not rollout:
            return
        device_id = str(record.get("device_id") or "")
        target = str(rollout.get("target_version") or "")
        running = str(record.get("firmware") or "")
        successful = result in {"install:complete", "install:boot-confirmed"} and running == target
        if successful:
            record["firmware_update_status"] = "verified"
            updated = rollout.setdefault("updated_devices", [])
            if device_id not in updated:
                updated.append(device_id)
            if rollout.get("canary_device_id") == device_id:
                rollout["status"] = "canary_verified"
                rollout["canary_verified_at"] = utc_now()
            elif rollout.get("status") == "fleet_active":
                rollout["last_verified_at"] = utc_now()
            rollout["last_verified_device_id"] = device_id
            return

        record["firmware_update_status"] = "failed"
        record["firmware_update_error"] = result
        rollout["last_failed_device_id"] = device_id
        rollout["last_failure"] = result
        rollout["last_failed_at"] = utc_now()
        rollout["status"] = "failed"

    def set_dashboard_page(
        self,
        device_id: str,
        index: int,
        count: int,
        title: str,
        titles: list[str] | None = None,
        profile: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            changed = record.get("dashboard_page_index") != index
            record["dashboard_page_index"] = index
            record["dashboard_page_number"] = index + 1
            record["dashboard_page_count"] = count
            record["dashboard_page_title"] = title
            if titles is not None:
                record["dashboard_pages"] = titles
            if profile is not None:
                record["dashboard_profile"] = profile
            if changed or not record.get("dashboard_page_changed_at"):
                record["dashboard_page_changed_at"] = utc_now()
            self._save()
            return deepcopy(record)

    def record_button_events(self, device_id: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Append newly received physical-button events and update summaries."""
        if not events:
            return self.get(device_id)
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            recent = record.setdefault("recent_button_events", [])
            known = {
                (
                    int(event.get("sequence") or 0),
                    str(event.get("button") or ""),
                    int(event.get("uptime_ms") or 0),
                )
                for event in recent
            }
            changed = False
            for event in events:
                identity = (
                    int(event.get("sequence") or 0),
                    str(event.get("button") or ""),
                    int(event.get("uptime_ms") or 0),
                )
                if identity in known:
                    continue
                received = {**event, "received_at": utc_now()}
                recent.append(received)
                known.add(identity)
                record["last_button"] = received["button"]
                record["last_button_action"] = received["action"]
                record["last_button_at"] = received["received_at"]
                record["button_press_count"] = int(record.get("button_press_count", 0)) + 1
                changed = True
            if changed:
                record["recent_button_events"] = recent[-32:]
                self._save()
            return deepcopy(record)

    def get(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._state["devices"].get(device_id)
            return deepcopy(record) if record else None

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(value) for value in self._state["devices"].values()]
