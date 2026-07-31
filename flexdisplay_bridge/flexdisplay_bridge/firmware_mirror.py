from __future__ import annotations

import hashlib
import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .config import FirmwareConfig


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class FirmwareMirrorError(RuntimeError):
    """Raised when the configured release cannot be mirrored safely."""


class FirmwareMirror:
    """Cache and verify the configured firmware release on the local Bridge."""

    def __init__(self, cache_dir: Path, packaged_firmware: Path | None = None):
        self.cache_dir = cache_dir
        self.packaged_firmware = packaged_firmware
        self._lock = threading.RLock()
        self._status: dict[str, Any] = {
            "enabled": True,
            "ready": False,
            "state": "empty",
            "last_error": "",
            "last_error_at": None,
            "last_ready_at": None,
        }
        self._next_retry_at: datetime | None = None

    def _path(self, firmware: FirmwareConfig) -> Path:
        digest = firmware.sha256 if len(firmware.sha256) == 64 else "unconfigured"
        return self.cache_dir / f"{digest}.bin"

    @staticmethod
    def _verify(path: Path, firmware: FirmwareConfig) -> bool:
        if not path.is_file() or path.stat().st_size != firmware.size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == firmware.sha256

    def status(self, firmware: FirmwareConfig) -> dict[str, Any]:
        with self._lock:
            result = dict(self._status)
            result["enabled"] = firmware.mirror_enabled
            result["version"] = firmware.version
            result["size"] = firmware.size
            result["cached_path"] = str(self._path(firmware)) if firmware.mirror_enabled else ""
            result["next_retry_at"] = (
                self._next_retry_at.isoformat(timespec="seconds")
                if self._next_retry_at
                else None
            )
            return result

    def prepare(self, firmware: FirmwareConfig, *, force: bool = False) -> Path:
        """Return a verified cache file, downloading it atomically when needed."""
        if not firmware.mirror_enabled:
            raise FirmwareMirrorError("The local firmware mirror is disabled")
        if (
            not firmware.version
            or len(firmware.sha256) != 64
            or firmware.size <= 0
        ):
            raise FirmwareMirrorError("The configured firmware manifest is incomplete")

        with self._lock:
            target = self._path(firmware)
            if not force and self._verify(target, firmware):
                self._status.update(
                    {
                        "ready": True,
                        "state": "ready",
                        "last_error": "",
                        "last_error_at": None,
                    }
                )
                return target

            if self.packaged_firmware and self._verify(
                self.packaged_firmware, firmware
            ):
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(".part")
                temporary.unlink(missing_ok=True)
                shutil.copyfile(self.packaged_firmware, temporary)
                if not self._verify(temporary, firmware):
                    temporary.unlink(missing_ok=True)
                    raise FirmwareMirrorError(
                        "The packaged firmware failed verification after copying"
                    )
                temporary.replace(target)
                self._next_retry_at = None
                self._status.update(
                    {
                        "ready": True,
                        "state": "ready",
                        "source": "packaged",
                        "last_error": "",
                        "last_error_at": None,
                        "last_ready_at": _utc_now(),
                    }
                )
                return target

            if not firmware.url.startswith(("http://", "https://")):
                raise FirmwareMirrorError("The configured firmware URL is invalid")

            now = datetime.now(UTC)
            if not force and self._next_retry_at and now < self._next_retry_at:
                wait = max(1, int((self._next_retry_at - now).total_seconds()))
                raise FirmwareMirrorError(f"Firmware mirror retry is delayed for {wait} seconds")

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".part")
            temporary.unlink(missing_ok=True)
            self._status.update({"ready": False, "state": "downloading"})

            try:
                digest = hashlib.sha256()
                downloaded = 0
                with requests.get(
                    firmware.url,
                    stream=True,
                    allow_redirects=True,
                    timeout=(10, 90),
                    headers={"User-Agent": "FlexDisplay-Bridge-Firmware-Mirror"},
                ) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as destination:
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if not chunk:
                                continue
                            downloaded += len(chunk)
                            if downloaded > firmware.size:
                                raise FirmwareMirrorError(
                                    "Firmware download exceeded the configured size"
                                )
                            digest.update(chunk)
                            destination.write(chunk)

                if downloaded != firmware.size:
                    raise FirmwareMirrorError(
                        f"Firmware size mismatch: received {downloaded}, expected {firmware.size}"
                    )
                if digest.hexdigest() != firmware.sha256:
                    raise FirmwareMirrorError("Firmware SHA-256 verification failed")

                temporary.replace(target)
                self._next_retry_at = None
                self._status.update(
                    {
                        "ready": True,
                        "state": "ready",
                        "source": "download",
                        "last_error": "",
                        "last_error_at": None,
                        "last_ready_at": _utc_now(),
                    }
                )
                return target
            except (OSError, requests.RequestException, FirmwareMirrorError) as err:
                temporary.unlink(missing_ok=True)
                self._next_retry_at = datetime.now(UTC) + timedelta(
                    seconds=firmware.mirror_retry_seconds
                )
                self._status.update(
                    {
                        "ready": False,
                        "state": "failed",
                        "last_error": str(err),
                        "last_error_at": _utc_now(),
                    }
                )
                if isinstance(err, FirmwareMirrorError):
                    raise
                raise FirmwareMirrorError(str(err)) from err
