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
                record["last_completed_commands"] = commands
                record["command_completed_at"] = utc_now()
                record["render_revision"] = int(record.get("render_revision", 0)) + 1
                self._save()
            return commands

    def get(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._state["devices"].get(device_id)
            return deepcopy(record) if record else None

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(value) for value in self._state["devices"].values()]
