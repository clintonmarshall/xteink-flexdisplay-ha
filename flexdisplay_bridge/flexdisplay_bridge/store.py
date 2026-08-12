from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHECKIN_HISTORY_LIMIT = 48
RESET_HISTORY_LIMIT = 24
IDENTITY_HISTORY_LIMIT = 24
PROBLEM_RESET_REASONS = {
    "panic",
    "interrupt_watchdog",
    "task_watchdog",
    "watchdog",
    "brownout",
}


def _is_android_receiver(record: dict[str, Any]) -> bool:
    model = str(record.get("model") or "").upper()
    normalized = "".join(character for character in model if character.isalnum())
    return normalized in {
        "ROOK",
        "ECHOSPOT",
        "ECHOSPOT2017",
        "AMAZONECHOSPOT",
        "CHECKERS",
        "ECHOSHOW5",
        "ECHOSHOW52019",
        "AMAZONECHOSHOW5",
    }


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DeviceStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"devices": {}}
        self._purged_device_ids: list[str] = []
        self._command_listeners: list[
            Callable[[str, str, dict[str, Any]], None]
        ] = []
        self._load()

    def add_command_listener(
        self, listener: Callable[[str, str, dict[str, Any]], None]
    ) -> None:
        """Notify a transport after a durable device command has been queued."""
        with self._lock:
            self._command_listeners.append(listener)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("devices"), dict):
                self._state = loaded
                self._migrate_legacy_commands()
                self._purge_legacy_invalid_devices()
        except (OSError, json.JSONDecodeError):
            self._state = {"devices": {}}

    def _purge_legacy_invalid_devices(self) -> None:
        """Remove identity-less records created by older Bridge releases."""
        devices = self._state["devices"]
        invalid = [
            device_id
            for device_id in devices
            if not device_id or device_id.upper() == "UNKNOWN"
        ]
        for device_id in invalid:
            devices.pop(device_id, None)
            self._remove_device_from_rollout(device_id)
        if invalid:
            self._purged_device_ids.extend(invalid)
            self._save()

    def _remove_device_from_rollout(self, device_id: str) -> None:
        rollout = self._state.get("firmware_rollout")
        if not isinstance(rollout, dict):
            return
        for field in ("planned_devices", "updated_devices"):
            values = rollout.get(field)
            if isinstance(values, list):
                rollout[field] = [value for value in values if value != device_id]
        if rollout.get("canary_device_id") == device_id:
            for field in (
                "canary_device_id",
                "canary_started_at",
                "canary_verified_at",
            ):
                rollout.pop(field, None)
            rollout["status"] = "awaiting_canary"
        if rollout.get("last_failed_device_id") == device_id:
            for field in (
                "last_failed_device_id",
                "last_failure",
                "last_failed_at",
            ):
                rollout.pop(field, None)

    @property
    def purged_device_ids(self) -> list[str]:
        return list(self._purged_device_ids)

    def remove_device(self, device_id: str) -> dict[str, Any] | None:
        """Permanently remove a fleet record and its active rollout references."""
        with self._lock:
            record = self._state["devices"].pop(device_id, None)
            if record is None:
                return None
            self._remove_device_from_rollout(device_id)
            self._save()
            return deepcopy(record)

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
            is_checkin = "firmware" in telemetry and "model" in telemetry
            previous_sd_ready = record.get("sd_ready")
            previous_boot_id = str(record.get("boot_id") or "")
            previous_model = str(record.get("model") or "")
            previous_model_reported = record.get("model_reported") is True
            now = utc_now()
            record.update({key: value for key, value in telemetry.items() if value is not None})
            record["last_seen"] = now
            if is_checkin:
                current_model = str(record.get("model") or "")
                current_model_reported = record.get("model_reported") is True
                if current_model and (
                    current_model != previous_model
                    or current_model_reported != previous_model_reported
                ):
                    identity = {
                        "at": now,
                        "from_model": previous_model,
                        "to_model": current_model,
                        "source": (
                            "reported" if current_model_reported else "inferred"
                        ),
                    }
                    identity_history = record.setdefault("identity_history", [])
                    identity_history.append(identity)
                    record["identity_history"] = identity_history[
                        -IDENTITY_HISTORY_LIMIT:
                    ]
                    record["last_identity_changed_at"] = now
                    record["last_identity_previous_model"] = previous_model
                record["checkin_count"] = int(record.get("checkin_count") or 0) + 1
                history = record.setdefault("checkin_history", [])
                point = {
                    "at": now,
                    "battery_percent": telemetry.get("battery_percent"),
                    "battery_voltage": telemetry.get("battery_voltage"),
                    "rssi": telemetry.get("rssi"),
                    "usb_connected": telemetry.get("usb_connected"),
                    "sd_ready": telemetry.get("sd_ready"),
                    "uptime_seconds": telemetry.get("uptime_seconds"),
                    "free_heap": telemetry.get("free_heap"),
                    "wake_reason": telemetry.get("wake_reason"),
                    "reset_reason": telemetry.get("reset_reason"),
                    "boot_id": telemetry.get("boot_id"),
                }
                history.append(
                    {key: value for key, value in point.items() if value is not None}
                )
                record["checkin_history"] = history[-CHECKIN_HISTORY_LIMIT:]

                if _is_android_receiver(telemetry):
                    # Android receivers intentionally report no SD card so an
                    # older Bridge fails closed instead of offering ESP32 OTA.
                    record["consecutive_sd_failures"] = 0
                    record["sd_failure_events"] = 0
                elif telemetry.get("sd_ready") is False:
                    record["consecutive_sd_failures"] = int(
                        record.get("consecutive_sd_failures") or 0
                    ) + 1
                    record["last_sd_failure_at"] = now
                    if previous_sd_ready is not False:
                        record["sd_failure_events"] = int(
                            record.get("sd_failure_events") or 0
                        ) + 1
                elif telemetry.get("sd_ready") is True:
                    record["consecutive_sd_failures"] = 0

                boot_id = str(telemetry.get("boot_id") or "")
                if boot_id and boot_id != previous_boot_id:
                    reason = str(telemetry.get("reset_reason") or "unknown")
                    record["boot_id"] = boot_id
                    record["reset_reason"] = reason
                    record["reset_count"] = int(record.get("reset_count") or 0) + 1
                    if "watchdog" in reason:
                        record["watchdog_reset_count"] = int(
                            record.get("watchdog_reset_count") or 0
                        ) + 1
                    if reason == "panic":
                        record["panic_reset_count"] = int(
                            record.get("panic_reset_count") or 0
                        ) + 1
                    if reason == "brownout":
                        record["brownout_reset_count"] = int(
                            record.get("brownout_reset_count") or 0
                        ) + 1
                    resets = record.setdefault("reset_history", [])
                    resets.append(
                        {
                            "at": now,
                            "boot_id": boot_id,
                            "reason": reason,
                            "wake_reason": telemetry.get("wake_reason"),
                        }
                    )
                    record["reset_history"] = resets[-RESET_HISTORY_LIMIT:]
                    if reason in PROBLEM_RESET_REASONS:
                        record["last_problem_reset_at"] = now
                        record["last_problem_reset_reason"] = reason
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

    def remove_provisioning_fields(
        self,
        device_id: str,
        fields: set[str],
        *,
        reason: str = "capability-reconciled",
    ) -> dict[str, Any] | None:
        """Drop assignments that a corrected device identity cannot apply."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            removed = sorted(field for field in fields if field in record)
            if not removed:
                return deepcopy(record)
            for field in removed:
                record.pop(field, None)
            record["provisioning_capability_reconciled_at"] = utc_now()
            record["provisioning_capability_reconciled_reason"] = str(reason)[:96]
            record["provisioning_capability_removed_fields"] = removed
            record["render_revision"] = int(record.get("render_revision", 0)) + 1
            self._save()
            return deepcopy(record)

    def next_policy_revision(self) -> int:
        """Allocate one monotonic revision for an atomic fleet policy change."""
        with self._lock:
            revision = int(self._state.get("policy_revision_sequence") or 0) + 1
            self._state["policy_revision_sequence"] = revision
            self._save()
            return revision

    def custom_policy_profiles(self) -> dict[str, dict[str, Any]]:
        """Return operator-created fleet policy profiles."""
        with self._lock:
            return deepcopy(self._state.get("custom_policy_profiles") or {})

    def fleet_groups(self) -> dict[str, dict[str, Any]]:
        """Return saved explicit or filter-based device cohorts."""
        with self._lock:
            return deepcopy(self._state.get("fleet_groups") or {})

    def put_fleet_group(
        self,
        group_id: str,
        *,
        label: str,
        description: str,
        device_ids: list[str],
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or replace a reusable fleet cohort."""
        with self._lock:
            groups = self._state.setdefault("fleet_groups", {})
            now = utc_now()
            previous = groups.get(group_id) or {}
            group = {
                "id": group_id,
                "label": label,
                "description": description,
                "device_ids": sorted(set(device_ids)),
                "filters": deepcopy(filters),
                "created_at": previous.get("created_at") or now,
                "updated_at": now,
            }
            groups[group_id] = group
            self._save()
            return deepcopy(group)

    def delete_fleet_group(self, group_id: str) -> bool:
        """Delete a saved fleet cohort."""
        with self._lock:
            groups = self._state.get("fleet_groups") or {}
            if group_id not in groups:
                return False
            del groups[group_id]
            self._save()
            return True

    def put_custom_policy_profile(
        self,
        profile_id: str,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            profiles = self._state.setdefault("custom_policy_profiles", {})
            stored = deepcopy(profile)
            stored["id"] = profile_id
            stored["updated_at"] = utc_now()
            stored["created_at"] = str(
                (profiles.get(profile_id) or {}).get("created_at") or stored["updated_at"]
            )
            profiles[profile_id] = stored
            self._save()
            return deepcopy(stored)

    def delete_custom_policy_profile(self, profile_id: str) -> bool:
        with self._lock:
            profiles = self._state.get("custom_policy_profiles") or {}
            if profile_id not in profiles:
                return False
            del profiles[profile_id]
            self._save()
            return True

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
            queued = deepcopy(record)
            listeners = list(self._command_listeners)
        # Transport callbacks can acquire their own locks or publish over the
        # network, so never invoke them while the state-file lock is held.
        for listener in listeners:
            listener(device_id, command, deepcopy(queued))
        return queued

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
                if "install" in commands:
                    record["firmware_update_status"] = "dispatched"
                    record["firmware_update_stage"] = "dispatched"
                    record["firmware_update_percent"] = 1
                    record["firmware_update_stage_at"] = record["command_dispatched_at"]
                record["render_revision"] = int(record.get("render_revision", 0)) + 1
                self._save()
            return commands

    def clear_commands(
        self,
        device_id: str,
        *,
        include_dispatched: bool = True,
    ) -> dict[str, Any] | None:
        """Cancel queued commands and optionally stop durable command retries."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            now = utc_now()
            cancelled_pending = list(record.get("pending_commands") or [])
            cancelled_dispatched = (
                list(record.get("dispatched_commands") or []) if include_dispatched else []
            )
            cancelled_id = str(
                record.get("dispatched_command_id")
                or record.get("pending_command_id")
                or ""
            )
            record["pending_commands"] = []
            record.pop("pending_command_id", None)
            if include_dispatched:
                record["dispatched_commands"] = []
                record.pop("dispatched_command_id", None)
            record["commands_cancelled_at"] = now
            record["last_cancelled_commands"] = list(
                dict.fromkeys(cancelled_pending + cancelled_dispatched)
            )
            record["last_cancelled_command_id"] = cancelled_id[:96]
            if "install" in cancelled_pending or "install" in cancelled_dispatched:
                record["firmware_update_status"] = "cancelled"
                record["firmware_update_stage"] = "cancelled"
                record["firmware_update_percent"] = 0
                record["firmware_update_stage_at"] = now
                record["firmware_update_error"] = "install:cancelled"
                record["firmware_update_error_at"] = now
                rollout = self._state.get("firmware_rollout") or {}
                if rollout.get("target_version") == record.get("firmware_update_target"):
                    rollout["last_cancelled_device_id"] = device_id
                    rollout["last_cancelled_at"] = now
                    if rollout.get("canary_device_id") == device_id:
                        rollout["status"] = "awaiting_canary"
                        rollout.pop("canary_device_id", None)
                        rollout.pop("canary_started_at", None)
            self._save()
            return deepcopy(record)

    def remove_command(
        self,
        device_id: str,
        command: str,
        *,
        reason: str = "capability-excluded",
    ) -> dict[str, Any] | None:
        """Remove one unsafe queued/dispatched command without losing others."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            pending = list(record.get("pending_commands") or [])
            dispatched = list(record.get("dispatched_commands") or [])
            if command not in pending and command not in dispatched:
                return deepcopy(record)
            record["pending_commands"] = [item for item in pending if item != command]
            record["dispatched_commands"] = [
                item for item in dispatched if item != command
            ]
            if not record["pending_commands"]:
                record.pop("pending_command_id", None)
            if not record["dispatched_commands"]:
                record.pop("dispatched_command_id", None)
            now = utc_now()
            record["commands_cancelled_at"] = now
            record["last_cancelled_commands"] = [command]
            if command == "install":
                record["firmware_update_status"] = "cancelled"
                record["firmware_update_stage"] = "cancelled"
                record["firmware_update_percent"] = 0
                record["firmware_update_stage_at"] = now
                record["firmware_update_error"] = f"install:{reason}"[:160]
                record["firmware_update_error_at"] = now
                rollout = self._state.get("firmware_rollout") or {}
                if rollout.get("target_version") == record.get(
                    "firmware_update_target"
                ):
                    rollout["last_cancelled_device_id"] = device_id
                    rollout["last_cancelled_at"] = now
                    if rollout.get("canary_device_id") == device_id:
                        rollout["status"] = "awaiting_canary"
                        rollout.pop("canary_device_id", None)
                        rollout.pop("canary_started_at", None)
            self._save()
            return deepcopy(record)

    def record_firmware_progress(
        self,
        device_id: str,
        command_id: str,
        stage: str,
        percent: int,
        detail: str = "",
    ) -> tuple[dict[str, Any] | None, bool]:
        """Record OTA progress and tell a device whether its command was cancelled."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None, True
            active_id = str(record.get("dispatched_command_id") or "")
            active = bool(
                command_id
                and active_id == command_id
                and "install" in (record.get("dispatched_commands") or [])
            )
            if not active:
                record["last_cancelled_progress_command_id"] = command_id[:96]
                record["last_cancelled_progress_at"] = utc_now()
                self._save()
                return deepcopy(record), True

            now = utc_now()
            record["firmware_update_status"] = stage
            record["firmware_update_stage"] = stage
            record["firmware_update_percent"] = max(0, min(100, int(percent)))
            record["firmware_update_stage_at"] = now
            if detail:
                record["firmware_update_detail"] = detail[:160]
            if stage == "failed":
                record["firmware_update_error"] = detail[:160] or "install:failed"
                record["firmware_update_error_at"] = now
            history = record.setdefault("firmware_progress_history", [])
            history.append(
                {
                    "command_id": command_id[:96],
                    "stage": stage,
                    "percent": record["firmware_update_percent"],
                    "detail": detail[:160],
                    "at": now,
                }
            )
            record["firmware_progress_history"] = history[-24:]
            self._save()
            return deepcopy(record), False

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

    def record_management_result(
        self,
        device_id: str,
        action: str,
        success: bool,
        detail: str,
    ) -> dict[str, Any] | None:
        """Persist the outcome of an App-only MQTT or Studio action."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            now = utc_now()
            result = {
                "action": str(action)[:64],
                "success": bool(success),
                "detail": str(detail)[:240],
                "at": now,
            }
            record["last_management_action"] = result["action"]
            record["last_management_action_success"] = result["success"]
            record["last_management_action_detail"] = result["detail"]
            record["last_management_action_at"] = now
            history = record.setdefault("management_history", [])
            history.append(result)
            record["management_history"] = history[-24:]
            self._save()
            return deepcopy(record)

    def set_screen_override(
        self,
        device_id: str,
        history_id: str,
    ) -> dict[str, Any] | None:
        """Queue an exact historical image for the device's next check-in."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            record["screen_override_id"] = str(history_id)[:96]
            record["screen_override_queued_at"] = utc_now()
            self._save()
            return deepcopy(record)

    def clear_screen_override(
        self,
        device_id: str,
        history_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            if record.get("screen_override_id") == history_id:
                record.pop("screen_override_id", None)
                record["screen_override_sent_at"] = utc_now()
                self._save()
            return deepcopy(record)

    def expire_stale_firmware_installs(self, timeout_seconds: int) -> list[str]:
        """Release installs that stopped reporting progress for a bounded time."""
        timeout = max(300, int(timeout_seconds))
        with self._lock:
            now = datetime.now(UTC)
            expired: list[str] = []
            for device_id, record in self._state["devices"].items():
                # A queued command may legitimately wait for a sleeping display
                # for hours. Only expire an install after it has been delivered
                # and the device then stops reporting progress.
                active = "install" in (record.get("dispatched_commands") or [])
                if not active:
                    continue
                last_activity = (
                    record.get("firmware_update_stage_at")
                    or record.get("command_dispatched_at")
                    or record.get("command_queued_at")
                )
                try:
                    age = (now - datetime.fromisoformat(str(last_activity))).total_seconds()
                except (TypeError, ValueError):
                    continue
                if age < timeout:
                    continue
                expired.append(device_id)
                record["pending_commands"] = [
                    command
                    for command in (record.get("pending_commands") or [])
                    if command != "install"
                ]
                if not record["pending_commands"]:
                    record.pop("pending_command_id", None)
                record["dispatched_commands"] = [
                    command
                    for command in (record.get("dispatched_commands") or [])
                    if command != "install"
                ]
                if not record["dispatched_commands"]:
                    record.pop("dispatched_command_id", None)
                expired_at = utc_now()
                record["firmware_update_status"] = "failed"
                record["firmware_update_stage"] = "failed"
                record["firmware_update_percent"] = 0
                record["firmware_update_stage_at"] = expired_at
                record["firmware_update_error"] = "install:stale-timeout"
                record["firmware_update_error_at"] = expired_at
                record["firmware_stale_cleared_at"] = expired_at
                record["firmware_stale_timeout_seconds"] = timeout
                history = record.setdefault("firmware_progress_history", [])
                history.append(
                    {
                        "command_id": str(record.get("last_command_id") or "")[:96],
                        "stage": "failed",
                        "percent": 0,
                        "detail": "install:stale-timeout",
                        "at": expired_at,
                    }
                )
                record["firmware_progress_history"] = history[-24:]
                rollout = self._state.get("firmware_rollout") or {}
                if rollout.get("target_version") == record.get("firmware_update_target"):
                    rollout["status"] = "failed"
                    rollout["last_failed_device_id"] = device_id
                    rollout["last_failure"] = "install:stale-timeout"
                    rollout["last_failed_at"] = expired_at
            if expired:
                self._save()
            return expired

    def firmware_rollout(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state.get("firmware_rollout") or {})

    def plan_firmware_rollout(
        self,
        target_version: str,
        device_ids: list[str],
        *,
        scope: str,
        canary_required: bool,
    ) -> dict[str, Any]:
        """Persist the selected rollout scope so it can advance after acknowledgements."""
        if not target_version:
            raise ValueError("A target firmware version is required")
        planned = list(dict.fromkeys(device_ids))
        if not planned:
            raise ValueError("At least one rollout device is required")
        with self._lock:
            rollout = self._state.get("firmware_rollout") or {}
            if rollout.get("target_version") != target_version:
                rollout = {
                    "target_version": target_version,
                    "status": "awaiting_canary" if canary_required else "fleet_active",
                    "started_at": utc_now(),
                    "updated_devices": [],
                }
                self._state["firmware_rollout"] = rollout
            rollout["planned_devices"] = planned
            rollout["scope"] = scope
            rollout["auto_continue"] = True
            rollout["plan_updated_at"] = utc_now()
            rollout.setdefault("plan_started_at", rollout["plan_updated_at"])
            self._save()
            return deepcopy(rollout)

    def reset_firmware_rollout(
        self,
        target_version: str,
        *,
        canary_required: bool,
    ) -> dict[str, Any]:
        """Cancel active installs and create a clean rollout for the same release."""
        if not target_version:
            raise ValueError("A target firmware version is required")
        with self._lock:
            now = utc_now()
            previous = deepcopy(self._state.get("firmware_rollout") or {})
            history = list(self._state.get("firmware_rollout_history") or [])
            if previous:
                previous["archived_at"] = now
                history.append(previous)
                self._state["firmware_rollout_history"] = history[-12:]

            for record in self._state["devices"].values():
                pending = list(record.get("pending_commands") or [])
                dispatched = list(record.get("dispatched_commands") or [])
                if "install" in pending:
                    record["pending_commands"] = [
                        command for command in pending if command != "install"
                    ]
                    if not record["pending_commands"]:
                        record.pop("pending_command_id", None)
                if "install" in dispatched:
                    record["dispatched_commands"] = [
                        command for command in dispatched if command != "install"
                    ]
                    if not record["dispatched_commands"]:
                        record.pop("dispatched_command_id", None)
                if "install" in pending or "install" in dispatched:
                    record["firmware_update_status"] = "cancelled"
                    record["firmware_update_stage"] = "cancelled"
                    record["firmware_update_percent"] = 0
                    record["firmware_update_stage_at"] = now
                    record["firmware_update_error"] = "install:rollout-reset"
                    record["firmware_update_error_at"] = now

            rollout = {
                "target_version": target_version,
                "status": "awaiting_canary" if canary_required else "fleet_active",
                "started_at": now,
                "reset_at": now,
                "updated_devices": [],
            }
            self._state["firmware_rollout"] = rollout
            self._save()
            return deepcopy(rollout)

    def retry_firmware_install(
        self,
        device_id: str,
        target_version: str,
        *,
        canary_required: bool,
        max_parallel: int,
        retry_limit: int,
        retry_backoff_seconds: int,
    ) -> dict[str, Any]:
        """Retry one failed install while preserving canary and concurrency gates."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                raise ValueError("Device has not checked in")
            if record.get("firmware_update_status") not in {"failed", "cancelled"}:
                raise ValueError(
                    "The device has no failed or cancelled firmware update to retry"
                )
            retry_count = int(record.get("firmware_retry_count") or 0)
            if retry_count >= retry_limit:
                raise ValueError(f"Firmware retry limit of {retry_limit} has been reached")
            last_attempt = record.get("firmware_last_retry_at") or record.get(
                "firmware_update_error_at"
            )
            if retry_backoff_seconds > 0 and last_attempt:
                try:
                    elapsed = (
                        datetime.now(UTC) - datetime.fromisoformat(str(last_attempt))
                    ).total_seconds()
                    if elapsed < retry_backoff_seconds:
                        remaining = max(1, int(retry_backoff_seconds - elapsed))
                        raise ValueError(
                            f"Firmware retry is delayed for {remaining} seconds"
                        )
                except ValueError as err:
                    if "delayed" in str(err):
                        raise

            rollout = self._state.get("firmware_rollout") or {}
            if rollout.get("target_version") != target_version:
                rollout = {
                    "target_version": target_version,
                    "status": "awaiting_canary" if canary_required else "fleet_active",
                    "started_at": utc_now(),
                    "updated_devices": [],
                }
                self._state["firmware_rollout"] = rollout
            elif rollout.get("status") == "failed":
                if canary_required:
                    rollout["status"] = "canary_active"
                    rollout["canary_device_id"] = device_id
                    rollout["canary_started_at"] = utc_now()
                else:
                    rollout["status"] = "fleet_active"
                rollout["retry_resumed_at"] = utc_now()
                rollout["retry_device_id"] = device_id

            record.pop("firmware_update_error", None)
            record.pop("firmware_update_error_at", None)
            record["firmware_retry_count"] = retry_count + 1
            record["firmware_last_retry_at"] = utc_now()
            self._save()
            return self.queue_firmware_install(
                device_id,
                target_version,
                canary_required=canary_required,
                max_parallel=max_parallel,
            )

    def reconcile_running_firmware(
        self,
        device_id: str,
        target_version: str,
        *,
        canary_required: bool,
        require_usb_for_canary: bool,
        method: str = "device_checkin",
    ) -> dict[str, Any] | None:
        """Reconcile an exact target version reported after OTA or a USB flash."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record or not target_version or record.get("firmware") != target_version:
                return deepcopy(record) if record else None
            if record.get("firmware_update_status") == "verified":
                successful_ack = record.get("last_command_result") in {
                    "install:complete",
                    "install:boot-confirmed",
                }
                tracked_target = str(record.get("firmware_update_target") or "")
                completed_at = str(record.get("command_completed_at") or "")
                verified_at = str(record.get("firmware_verified_at") or "")
                stale_evidence = (
                    record.get("firmware_verification_method") != "device_checkin"
                    or not verified_at
                    or (completed_at and verified_at < completed_at)
                )
                if successful_ack and tracked_target == target_version and stale_evidence:
                    current_verification = completed_at or utc_now()
                    record["firmware_verified_at"] = current_verification
                    record["firmware_verification_method"] = "device_checkin"
                    record["firmware_update_stage_at"] = current_verification
                    history = record.get("command_history") or []
                    if history and history[-1].get("result") == record.get(
                        "last_command_result"
                    ):
                        history[-1]["verification_method"] = "device_checkin"
                    rollout = self._state.get("firmware_rollout") or {}
                    if rollout.get("target_version") == target_version:
                        rollout["last_verified_at"] = current_verification
                        rollout["last_verified_device_id"] = device_id
                        rollout["last_verification_method"] = "device_checkin"
                    self._save()
                return deepcopy(record)

            tracked_target = str(record.get("firmware_update_target") or "")
            rollout = self._state.get("firmware_rollout") or {}
            rollout_matches = rollout.get("target_version") == target_version
            was_tracked = bool(
                tracked_target == target_version
                or "install" in (record.get("pending_commands") or [])
                or "install" in (record.get("dispatched_commands") or [])
                or record.get("firmware_update_status")
                in {
                    "queued",
                    "dispatched",
                    "downloading",
                    "validating",
                    "flashing",
                    "rebooting",
                    "failed",
                    "cancelled",
                }
            )
            if not was_tracked:
                return deepcopy(record)

            usb_eligible = record.get("usb_connected") is True
            can_recover_rollout = rollout_matches and (
                not canary_required
                or rollout.get("canary_device_id") in {None, "", device_id}
                and (not require_usb_for_canary or usb_eligible)
            )
            now = utc_now()
            reconciled_command_id = str(
                record.get("dispatched_command_id")
                or record.get("pending_command_id")
                or record.get("last_command_id")
                or ""
            )
            record["pending_commands"] = [
                command
                for command in (record.get("pending_commands") or [])
                if command != "install"
            ]
            if not record["pending_commands"]:
                record.pop("pending_command_id", None)
            record["dispatched_commands"] = [
                command
                for command in (record.get("dispatched_commands") or [])
                if command != "install"
            ]
            if not record["dispatched_commands"]:
                record.pop("dispatched_command_id", None)
            record["firmware_update_target"] = target_version
            record["firmware_update_status"] = "verified"
            record["firmware_update_stage"] = "verified"
            record["firmware_update_percent"] = 100
            record["firmware_update_stage_at"] = now
            record["firmware_verified_at"] = now
            record["firmware_verification_method"] = method
            if reconciled_command_id:
                record["last_command_id"] = reconciled_command_id[:96]
                record["last_command_result"] = "install:boot-reconciled"
            record.pop("firmware_update_error", None)
            record.pop("firmware_update_error_at", None)

            if rollout_matches:
                updated = rollout.setdefault("updated_devices", [])
                if device_id not in updated:
                    updated.append(device_id)
                if can_recover_rollout and canary_required:
                    rollout["canary_device_id"] = device_id
                    rollout["status"] = "canary_verified"
                    rollout["canary_verified_at"] = now
                elif not canary_required and rollout.get("status") != "failed":
                    rollout["status"] = "fleet_active"
                rollout["last_verified_at"] = now
                rollout["last_verified_device_id"] = device_id
                rollout["last_verification_method"] = method
                if (
                    rollout.get("status") == "failed"
                    and rollout.get("last_failed_device_id") == device_id
                    and can_recover_rollout
                ):
                    rollout["status"] = (
                        "canary_verified" if canary_required else "fleet_active"
                    )

            history = record.setdefault("command_history", [])
            history.append(
                {
                    "command_id": reconciled_command_id[:96],
                    "result": "install:boot-reconciled",
                    "verification_method": method,
                    "completed_at": now,
                }
            )
            record["command_history"] = history[-16:]
            self._save()
            return deepcopy(record)

    def verify_usb_recovery(
        self,
        device_id: str,
        target_version: str,
        expected_command_id: str,
        *,
        canary_required: bool = True,
        max_checkin_age_seconds: int = 600,
        external_usb_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Reconcile a physical USB flash even when no durable install remains."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                raise ValueError("Device has not checked in")

            rollout = self._state.get("firmware_rollout") or {}
            dispatched = list(record.get("dispatched_commands") or [])
            dispatched_id = str(record.get("dispatched_command_id") or "")
            evidence = external_usb_evidence or {}
            usb_serial = re.sub(r"[^0-9A-F]", "", str(evidence.get("serial") or "").upper())
            device_suffix = re.sub(r"[^0-9A-F]", "", device_id.upper())[-6:]
            evidence_observed_at = str(evidence.get("observed_at") or "")
            external_usb_valid = bool(
                evidence.get("source") == "macos_ioreg"
                and usb_serial.endswith(device_suffix)
                and str(evidence.get("port") or "").startswith("/dev/cu.usbmodem")
                and re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("backup_sha256") or ""))
                and evidence_observed_at
            )
            blockers: list[str] = []
            if not target_version:
                blockers.append("A target firmware version is required")
            if record.get("firmware") != target_version:
                blockers.append("The device is not reporting the exact target firmware")
            if record.get("usb_connected") is not True and not external_usb_valid:
                blockers.append(
                    "USB power must be reported by the device or proven by matching external evidence"
                )
            if record.get("sd_ready") is not True:
                blockers.append("The device SD card is not ready")
            if any(
                command != "install"
                for command in (record.get("pending_commands") or [])
            ) or any(command != "install" for command in dispatched):
                blockers.append("The device has an unrelated command in progress")
            known_command_id = dispatched_id or str(record.get("last_command_id") or "")
            if (
                expected_command_id
                and known_command_id
                and known_command_id != expected_command_id
            ):
                blockers.append("The expected command ID does not match the stuck install")

            last_seen = record.get("last_seen")
            try:
                seen = datetime.fromisoformat(str(last_seen))
                checkin_age = (datetime.now(UTC) - seen).total_seconds()
                if checkin_age < 0 or checkin_age > max_checkin_age_seconds:
                    blockers.append("The device check-in is not recent enough")
            except (TypeError, ValueError):
                blockers.append("The device has no valid recent check-in")
            if external_usb_valid:
                try:
                    observed = datetime.fromisoformat(evidence_observed_at)
                    evidence_age = (datetime.now(UTC) - observed).total_seconds()
                    if evidence_age < 0 or evidence_age > max_checkin_age_seconds:
                        blockers.append("The external USB observation is not recent enough")
                except (TypeError, ValueError):
                    blockers.append("The external USB observation time is invalid")

            if blockers:
                raise ValueError("; ".join(blockers))

            verified_at = utc_now()
            if rollout.get("target_version") != target_version:
                rollout = {
                    "target_version": target_version,
                    "status": "awaiting_canary" if canary_required else "fleet_active",
                    "started_at": verified_at,
                    "updated_devices": [],
                }
                self._state["firmware_rollout"] = rollout
            existing_canary = str(rollout.get("canary_device_id") or "")
            is_active_canary = canary_required and existing_canary in {"", device_id}
            verification_evidence = {
                "method": "usb_recovery",
                "role": "canary" if is_active_canary else "fleet",
                "device_id": device_id,
                "target_version": target_version,
                "observed_firmware": str(record.get("firmware") or ""),
                "observed_usb_connected": record.get("usb_connected") is True,
                "observed_sd_ready": True,
                "observed_last_seen": last_seen,
                "reconciled_command_id": dispatched_id or expected_command_id,
                "verified_at": verified_at,
            }
            if external_usb_valid:
                verification_evidence["external_usb_evidence"] = {
                    "source": "macos_ioreg",
                    "serial": usb_serial,
                    "port": str(evidence["port"]),
                    "backup_sha256": str(evidence["backup_sha256"]),
                    "observed_at": evidence_observed_at,
                }

            record["pending_commands"] = []
            record.pop("pending_command_id", None)
            record["dispatched_commands"] = []
            record.pop("dispatched_command_id", None)
            record["last_command_id"] = (dispatched_id or expected_command_id)[:96]
            record["last_command_result"] = "install:usb-recovery-verified"
            record["command_completed_at"] = verified_at
            record["firmware_update_status"] = "verified"
            record["firmware_update_stage"] = "verified"
            record["firmware_update_percent"] = 100
            record["firmware_update_stage_at"] = verified_at
            record["firmware_verification_method"] = "usb_recovery"
            record["firmware_verified_at"] = verified_at
            record["firmware_update_target"] = target_version
            record.pop("firmware_update_error", None)
            record.pop("firmware_update_error_at", None)
            record["last_usb_recovery_verification"] = verification_evidence
            history = record.setdefault("command_history", [])
            history.append(
                {
                    "command_id": (dispatched_id or expected_command_id)[:96],
                    "result": "install:usb-recovery-verified",
                    "verification_method": "usb_recovery",
                    "completed_at": verified_at,
                }
            )
            record["command_history"] = history[-16:]
            recovery_history = record.setdefault("usb_recovery_history", [])
            recovery_history.append(verification_evidence)
            record["usb_recovery_history"] = recovery_history[-8:]

            updated = rollout.setdefault("updated_devices", [])
            if device_id not in updated:
                updated.append(device_id)
            if is_active_canary:
                rollout["canary_device_id"] = device_id
                rollout["status"] = "canary_verified"
                rollout["canary_verified_at"] = verified_at
            elif rollout.get("status") == "canary_verified":
                rollout["status"] = "fleet_active"
            rollout["last_verified_at"] = verified_at
            rollout["last_verified_device_id"] = device_id
            rollout["last_verification_method"] = "usb_recovery"
            rollout_history = rollout.setdefault("verification_history", [])
            rollout_history.append(verification_evidence)
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
                    raise ValueError(
                        "Firmware rollout failed; retry the device, reset the rollout, "
                        "or configure a new release"
                    )
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
            record["firmware_update_provider"] = "xteink"
            record["firmware_update_target"] = target_version
            record["firmware_update_status"] = "queued"
            record["firmware_update_stage"] = "queued"
            record["firmware_update_percent"] = 0
            record["firmware_update_stage_at"] = utc_now()
            record.pop("firmware_update_error", None)
            record.pop("firmware_update_error_at", None)
            record.pop("firmware_update_detail", None)
            if not is_canary and rollout.get("status") == "canary_verified":
                rollout["status"] = "fleet_active"
            self._save()
            del queued
            return deepcopy(record)

    def queue_device_firmware_install(
        self,
        device_id: str,
        target_version: str,
        *,
        firmware_provider: str,
    ) -> dict[str, Any]:
        """Queue a model-specific OTA without changing the X3/X4 fleet rollout."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                raise ValueError("Device has not checked in")
            already_active = (
                "install" in (record.get("pending_commands") or [])
                or "install" in (record.get("dispatched_commands") or [])
            )
            if already_active:
                return deepcopy(record)
            self.queue_command(device_id, "install")
            record = self._state["devices"][device_id]
            record["firmware_update_role"] = "device"
            record["firmware_update_provider"] = str(firmware_provider or "")[:32]
            record["firmware_update_target"] = target_version
            record["firmware_update_status"] = "queued"
            record["firmware_update_stage"] = "queued"
            record["firmware_update_percent"] = 0
            record["firmware_update_stage_at"] = utc_now()
            record.pop("firmware_update_error", None)
            record.pop("firmware_update_error_at", None)
            record.pop("firmware_update_detail", None)
            self._save()
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
            verified_at = utc_now()
            verification_method = "device_checkin"
            record["firmware_update_status"] = "verified"
            record["firmware_update_stage"] = "verified"
            record["firmware_update_percent"] = 100
            record["firmware_update_stage_at"] = verified_at
            record["firmware_verified_at"] = verified_at
            record["firmware_verification_method"] = verification_method
            record.pop("firmware_update_error", None)
            record.pop("firmware_update_error_at", None)
            history = record.get("command_history") or []
            if history and history[-1].get("result") == result:
                history[-1]["verification_method"] = verification_method
            updated = rollout.setdefault("updated_devices", [])
            if device_id not in updated:
                updated.append(device_id)
            if rollout.get("canary_device_id") == device_id:
                rollout["status"] = "canary_verified"
                rollout["canary_verified_at"] = verified_at
            rollout["last_verified_at"] = verified_at
            rollout["last_verified_device_id"] = device_id
            rollout["last_verification_method"] = verification_method
            return

        record["firmware_update_status"] = "failed"
        record["firmware_update_stage"] = "failed"
        record["firmware_update_percent"] = 0
        record["firmware_update_stage_at"] = utc_now()
        record["firmware_update_error"] = result
        record["firmware_update_error_at"] = utc_now()
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
        selection: str | None = None,
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
            if selection is not None:
                record["dashboard_selection"] = selection
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
                record["last_button_gesture"] = received.get("gesture") or "short"
                record["last_button_at"] = received["received_at"]
                record["button_press_count"] = int(record.get("button_press_count", 0)) + 1
                changed = True
            if changed:
                record["recent_button_events"] = recent[-32:]
                self._save()
            return deepcopy(record)

    def set_button_actions(
        self,
        device_id: str,
        mappings: dict[str, dict[str, Any]],
        show_indicators: bool = False,
    ) -> dict[str, Any] | None:
        """Replace a device's validated physical-button action overrides."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            indicators_changed = (
                bool(record.get("button_action_indicators"))
                != bool(show_indicators)
            )
            if (
                record.get("button_action_mappings") == mappings
                and not indicators_changed
            ):
                return deepcopy(record)
            record["button_action_mappings"] = deepcopy(mappings)
            record["button_action_indicators"] = bool(show_indicators)
            record["button_actions_updated_at"] = utc_now()
            record["render_revision"] = int(record.get("render_revision", 0)) + 1
            self._save()
            return deepcopy(record)

    def record_button_action_result(
        self,
        device_id: str,
        event: dict[str, Any],
        action: dict[str, Any],
        success: bool,
        detail: str,
    ) -> dict[str, Any] | None:
        """Attach an execution result to a recorded gesture and its device summary."""
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return None
            identity = (
                int(event.get("sequence") or 0),
                str(event.get("button") or ""),
                int(event.get("uptime_ms") or 0),
            )
            result = {
                "type": str(action.get("type") or "none"),
                "source": str(action.get("source") or "default"),
                "success": success,
                "detail": detail[:240],
                "executed_at": utc_now(),
            }
            if action.get("command"):
                result["command"] = action["command"]
            if action.get("service"):
                result["service"] = action["service"]
            if action.get("entity_id"):
                result["entity_id"] = action["entity_id"]
            for recent in reversed(record.get("recent_button_events") or []):
                recent_identity = (
                    int(recent.get("sequence") or 0),
                    str(recent.get("button") or ""),
                    int(recent.get("uptime_ms") or 0),
                )
                if recent_identity == identity:
                    recent["configured_action"] = result
                    break
            record["last_button_action_result"] = detail[:240]
            record["last_button_action_success"] = success
            record["last_button_action_at"] = result["executed_at"]
            self._save()
            return deepcopy(record)

    def get(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._state["devices"].get(device_id)
            return deepcopy(record) if record else None

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(value) for value in self._state["devices"].values()]
