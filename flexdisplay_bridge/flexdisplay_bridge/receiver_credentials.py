from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


STORE_VERSION = 1


class ReceiverCredentialStateError(RuntimeError):
    """Raised when receiver revocation/rotation state cannot be trusted."""


class ReceiverCredentialStore:
    """Persist non-secret per-device epochs and revocation status."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    @staticmethod
    def _device_id(value: str) -> str:
        return str(value or "").strip().upper()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as err:
            raise ReceiverCredentialStateError(
                f"Receiver credential state is unreadable: {self.path}"
            ) from err
        if (
            not isinstance(raw, dict)
            or raw.get("version") != STORE_VERSION
            or not isinstance(raw.get("receivers"), dict)
        ):
            raise ReceiverCredentialStateError(
                f"Receiver credential state has an unsupported schema: {self.path}"
            )
        entries: dict[str, dict[str, Any]] = {}
        for device_id, value in raw["receivers"].items():
            if not isinstance(value, dict):
                raise ReceiverCredentialStateError("Receiver credential entry is invalid")
            selected_id = self._device_id(device_id)
            if not selected_id or selected_id in entries:
                raise ReceiverCredentialStateError(
                    "Receiver credential IDs contain an invalid case alias"
                )
            epoch = value.get("epoch", 0)
            disabled = value.get("disabled", False)
            if (
                isinstance(epoch, bool)
                or not isinstance(epoch, int)
                or not 0 <= epoch <= 0x7FFFFFFF
                or not isinstance(disabled, bool)
            ):
                raise ReceiverCredentialStateError("Receiver credential entry is invalid")
            entries[selected_id] = {
                "epoch": epoch,
                "disabled": disabled,
                "updated_at": str(value.get("updated_at") or "")[:40],
            }
        self._entries = entries

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        encoded = json.dumps(
            {"version": STORE_VERSION, "receivers": self._entries},
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
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
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def get(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(
                self._entries.get(
                    self._device_id(device_id),
                    {"epoch": 0, "disabled": False, "updated_at": ""},
                )
            )

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return deepcopy(dict(sorted(self._entries.items())))

    def revoke(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            selected_id = self._device_id(device_id)
            current = self.get(selected_id)
            proposed = {
                **self._entries,
                selected_id: {
                    **current,
                    "disabled": True,
                    "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            }
            previous = self._entries
            self._entries = proposed
            try:
                self._save()
            except OSError:
                self._entries = previous
                raise
            return self.get(selected_id)

    def rotate(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            selected_id = self._device_id(device_id)
            current = self.get(selected_id)
            if current["epoch"] >= 0x7FFFFFFF:
                raise ValueError("Receiver credential epoch is exhausted")
            proposed = {
                **self._entries,
                selected_id: {
                    "epoch": current["epoch"] + 1,
                    "disabled": False,
                    "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            }
            previous = self._entries
            self._entries = proposed
            try:
                self._save()
            except OSError:
                self._entries = previous
                raise
            return self.get(selected_id)
