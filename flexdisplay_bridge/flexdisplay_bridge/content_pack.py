from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import threading
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


MAX_PACK_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_FILES = 64
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MANAGED_PREFIXES = (
    "/factory-content/",
    "/photos/flexdisplay/",
    "/books/flexdisplay/",
    "/.crosspoint/fleet/",
)


class ContentPackError(ValueError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _safe_source(value: Any) -> str:
    source = str(value or "").lstrip("/")
    path = PurePosixPath(source)
    if not source or path.is_absolute() or ".." in path.parts:
        raise ContentPackError("Every content source must be a safe relative path")
    return str(path)


def _safe_target(value: Any) -> str:
    target = "/" + str(value or "").lstrip("/")
    path = PurePosixPath(target)
    if ".." in path.parts or not any(target.startswith(prefix) for prefix in MANAGED_PREFIXES):
        raise ContentPackError(
            "Content targets must use a managed FlexDisplay folder"
        )
    return str(path)


class ContentPackStore:
    def __init__(self, state_path: Path, content_root: Path):
        self.state_path = state_path
        self.content_root = content_root
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"packs": {}, "assignments": {}}
        self._load()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._state = {
                    "packs": loaded.get("packs") or {},
                    "assignments": loaded.get("assignments") or {},
                }
        except (OSError, json.JSONDecodeError):
            pass

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def payload(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def install(self, archive: bytes) -> dict[str, Any]:
        if not archive or len(archive) > MAX_PACK_BYTES:
            raise ContentPackError("Content pack must be between 1 byte and 32 MB")
        try:
            bundle = zipfile.ZipFile(io.BytesIO(archive))
        except zipfile.BadZipFile as err:
            raise ContentPackError("Content pack is not a valid ZIP file") from err
        names = bundle.namelist()
        if "content-pack.json" not in names:
            raise ContentPackError("ZIP must contain content-pack.json")
        if len(names) > MAX_FILES + 1:
            raise ContentPackError(f"Content packs support at most {MAX_FILES} files")
        try:
            supplied = json.loads(bundle.read("content-pack.json"))
        except (KeyError, json.JSONDecodeError) as err:
            raise ContentPackError("content-pack.json is invalid") from err
        version = str(supplied.get("version") or "")
        if not VERSION_PATTERN.fullmatch(version):
            raise ContentPackError("Content pack version is invalid")
        entries = supplied.get("files")
        if not isinstance(entries, list) or not entries:
            raise ContentPackError("Content pack must contain at least one file")

        files: list[dict[str, Any]] = []
        total = 0
        seen_sources: set[str] = set()
        seen_targets: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ContentPackError("Each content file entry must be an object")
            source = _safe_source(entry.get("source"))
            target = _safe_target(entry.get("target"))
            if source in seen_sources or target in seen_targets:
                raise ContentPackError("Content pack sources and targets must be unique")
            seen_sources.add(source)
            seen_targets.add(target)
            if source not in names:
                raise ContentPackError(f"ZIP is missing {source}")
            info = bundle.getinfo(source)
            if info.is_dir() or info.file_size > MAX_FILE_BYTES:
                raise ContentPackError(f"{source} is not a supported content file")
            content = bundle.read(source)
            total += len(content)
            if total > MAX_PACK_BYTES:
                raise ContentPackError("Expanded content pack is too large")
            files.append(
                {
                    "source": source,
                    "target": target,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )

        destination = self.content_root / version
        temporary = self.content_root / f".{version}.tmp"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        for item in files:
            output = temporary / item["source"]
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(bundle.read(item["source"]))
        if destination.exists():
            shutil.rmtree(destination)
        temporary.replace(destination)

        record = {
            "version": version,
            "name": str(supplied.get("name") or version)[:120],
            "description": str(supplied.get("description") or "")[:500],
            "created_at": now(),
            "file_count": len(files),
            "size": total,
            "files": files,
        }
        with self._lock:
            self._state["packs"][version] = record
            self._save()
        return deepcopy(record)

    def assign(self, version: str, device_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            if version not in self._state["packs"]:
                raise ContentPackError("Unknown content pack")
            for device_id in device_ids:
                self._state["assignments"][device_id] = {
                    "desired_version": version,
                    "status": "pending",
                    "error": "",
                    "assigned_at": now(),
                    "updated_at": now(),
                }
            self._save()
            return deepcopy(self._state["assignments"])

    def observe(
        self, device_id: str, installed_version: str, status: str, error: str
    ) -> dict[str, Any] | None:
        with self._lock:
            assignment = self._state["assignments"].get(device_id)
            if not assignment:
                return None
            if installed_version:
                assignment["installed_version"] = installed_version[:64]
            if status:
                assignment["status"] = status[:32]
            if error:
                assignment["error"] = error[:240]
            elif status == "installed":
                assignment["error"] = ""
            assignment["updated_at"] = now()
            self._save()
            return deepcopy(assignment)

    def desired(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            assignment = self._state["assignments"].get(device_id)
            if not assignment:
                return None
            if (
                assignment.get("installed_version") == assignment.get("desired_version")
                and assignment.get("status") == "installed"
            ):
                return None
            return deepcopy(assignment)

    def manifest(self, version: str, base_url: str) -> tuple[bytes, str]:
        with self._lock:
            pack = self._state["packs"].get(version)
            if not pack:
                raise ContentPackError("Unknown content pack")
            files = deepcopy(pack["files"])
        prefix = base_url.rstrip("/")
        for item in files:
            item["url"] = (
                f"{prefix}/api/v1/content-packs/{version}/files/{item['source']}"
            )
        content = json.dumps(
            {"format": 1, "version": version, "files": files},
            separators=(",", ":"),
        ).encode()
        return content, hashlib.sha256(content).hexdigest()

    def file_path(self, version: str, source: str) -> Path:
        selected = _safe_source(source)
        with self._lock:
            pack = self._state["packs"].get(version)
            if not pack or selected not in {item["source"] for item in pack["files"]}:
                raise ContentPackError("Unknown content pack file")
        path = (self.content_root / version / selected).resolve()
        if not path.is_relative_to((self.content_root / version).resolve()):
            raise ContentPackError("Unsafe content pack file")
        return path
