from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ScreenHistoryError(ValueError):
    """Raised when a screen-history item cannot be used."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _safe(value: str) -> str:
    selected = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    if not selected:
        raise ScreenHistoryError("Invalid screen-history identifier")
    return selected[:96]


class ScreenHistoryStore:
    """Keep a small, durable preview history for every e-paper display."""

    def __init__(self, root: Path, limit: int = 5):
        self.root = root
        self.limit = max(1, min(20, int(limit)))
        self._lock = threading.RLock()

    def _device_root(self, device_id: str) -> Path:
        return self.root / _safe(device_id)

    def _index_path(self, device_id: str) -> Path:
        return self._device_root(device_id) / "index.json"

    def _load(self, device_id: str) -> list[dict[str, Any]]:
        path = self._index_path(device_id)
        if not path.exists():
            return []
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [item for item in loaded if isinstance(item, dict)] if isinstance(loaded, list) else []

    def _save(self, device_id: str, items: list[dict[str, Any]]) -> None:
        path = self._index_path(device_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(items, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    def record(
        self,
        device_id: str,
        content: bytes,
        *,
        media_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not content:
            raise ScreenHistoryError("A rendered screen is required")
        with self._lock:
            digest = hashlib.sha256(content).hexdigest()
            items = self._load(device_id)
            if items and items[0].get("sha256") == digest:
                return dict(items[0])
            captured_at = _utc_now()
            extension = "bmp" if media_type == "image/bmp" else "png"
            history_id = _safe(
                f"{captured_at.replace(':', '').replace('+', '-')}-{digest[:12]}"
            )
            filename = f"{history_id}.{extension}"
            device_root = self._device_root(device_id)
            device_root.mkdir(parents=True, exist_ok=True)
            temporary = (device_root / filename).with_suffix(f".{extension}.tmp")
            temporary.write_bytes(content)
            temporary.replace(device_root / filename)
            item = {
                "id": history_id,
                "captured_at": captured_at,
                "sha256": digest,
                "media_type": media_type,
                "size": len(content),
                "filename": filename,
                **{
                    key: value
                    for key, value in (metadata or {}).items()
                    if value is not None and key not in {"filename", "path"}
                },
            }
            kept = [item, *items[: self.limit - 1]]
            for stale in items[self.limit - 1 :]:
                stale_name = str(stale.get("filename") or "")
                if stale_name and Path(stale_name).name == stale_name:
                    try:
                        (device_root / stale_name).unlink(missing_ok=True)
                    except OSError:
                        pass
            self._save(device_id, kept)
            return dict(item)

    def list(self, device_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._load(device_id)]

    def get(self, device_id: str, history_id: str) -> tuple[Path, dict[str, Any]]:
        selected = _safe(history_id)
        with self._lock:
            item = next(
                (entry for entry in self._load(device_id) if entry.get("id") == selected),
                None,
            )
            if not item:
                raise ScreenHistoryError("Screen-history item not found")
            filename = str(item.get("filename") or "")
            if not filename or Path(filename).name != filename:
                raise ScreenHistoryError("Stored screen filename is invalid")
            path = self._device_root(device_id) / filename
            if not path.is_file():
                raise ScreenHistoryError("Stored screen image is missing")
            return path, dict(item)

    def latest(self, device_id: str) -> tuple[Path, dict[str, Any]]:
        with self._lock:
            items = self._load(device_id)
            if not items:
                raise ScreenHistoryError("No screen history is available")
            return self.get(device_id, str(items[0]["id"]))
