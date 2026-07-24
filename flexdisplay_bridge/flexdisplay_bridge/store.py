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
        except (OSError, json.JSONDecodeError):
            self._state = {"devices": {}}

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
                record["command_dispatched_at"] = utc_now()
                record["render_revision"] = int(record.get("render_revision", 0)) + 1
                self._save()
            return commands

    def acknowledge(self, device_id: str, result: str) -> None:
        if not result:
            return
        with self._lock:
            record = self._state["devices"].get(device_id)
            if not record:
                return
            record["last_command_result"] = result[:160]
            record["command_completed_at"] = utc_now()
            record["dispatched_commands"] = []
            self._save()

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
