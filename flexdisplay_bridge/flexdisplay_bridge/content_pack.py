from __future__ import annotations

import base64
import binascii
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
MAX_CARDS = 32
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MANAGED_PREFIXES = (
    "/factory-content/",
    "/photos/flexdisplay/",
    "/books/flexdisplay/",
    "/cards/flexdisplay/",
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
        self._state: dict[str, Any] = {
            "packs": {},
            "assignments": {},
            "deployments": [],
        }
        self._load()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._state = {
                    "packs": loaded.get("packs") or {},
                    "assignments": loaded.get("assignments") or {},
                    "deployments": loaded.get("deployments") or [],
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
            "kind": str(supplied.get("kind") or "assets")[:32],
            "created_at": now(),
            "file_count": len(files),
            "size": total,
            "files": files,
        }
        with self._lock:
            self._state["packs"][version] = record
            self._save()
        return deepcopy(record)

    def build_quick_cards(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Build an offline Quick Cards content pack without requiring a ZIP tool."""
        version = str(payload.get("version") or "").strip()
        if not VERSION_PATTERN.fullmatch(version):
            raise ContentPackError("Content pack version is invalid")
        cards = payload.get("cards")
        if not isinstance(cards, list) or not cards:
            raise ContentPackError("Add at least one Quick Card")
        if len(cards) > MAX_CARDS:
            raise ContentPackError(f"Quick Card packs support at most {MAX_CARDS} cards")

        normalised_cards: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        allowed_types = {"id_badge", "contact", "wifi", "message", "emergency", "image"}
        for supplied in cards:
            if not isinstance(supplied, dict):
                raise ContentPackError("Every Quick Card must be an object")
            card_id = str(supplied.get("id") or "").strip()
            card_type = str(supplied.get("type") or "message").strip().lower()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}", card_id):
                raise ContentPackError("Every Quick Card needs a safe, unique ID")
            if card_id in seen_ids:
                raise ContentPackError(f"Duplicate Quick Card ID: {card_id}")
            if card_type not in allowed_types:
                raise ContentPackError(f"Unsupported Quick Card type: {card_type}")
            seen_ids.add(card_id)
            card = {"id": card_id, "type": card_type}
            for field, limit in {
                "title": 80,
                "subtitle": 120,
                "body": 512,
                "footer": 120,
                "qr_payload": 800,
                "qr_label": 80,
                "image_path": 180,
                "expires": 40,
            }.items():
                value = str(supplied.get(field) or "").strip()
                if value:
                    card[field] = value[:limit]
            if not card.get("title"):
                raise ContentPackError(f"Quick Card {card_id} needs a title")
            image_path = str(card.get("image_path") or "")
            if image_path and (
                not image_path.startswith("/cards/flexdisplay/") or ".." in image_path
            ):
                raise ContentPackError(
                    f"Quick Card {card_id} image must use /cards/flexdisplay/"
                )
            if bool(supplied.get("favourite")):
                card["favourite"] = True
            normalised_cards.append(card)

        assets = payload.get("assets") or []
        if not isinstance(assets, list):
            raise ContentPackError("Quick Card assets must be a list")
        files: dict[str, bytes] = {
            "quick-cards/cards.json": (
                json.dumps(
                    {
                        "schema_version": 1,
                        "version": version,
                        "title": str(payload.get("name") or version)[:80],
                        "cards": normalised_cards,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
        }
        manifest_files: list[dict[str, str]] = [
            {
                "source": "quick-cards/cards.json",
                "target": "/cards/flexdisplay/cards.json",
            }
        ]
        for supplied in assets:
            if not isinstance(supplied, dict):
                raise ContentPackError("Every Quick Card asset must be an object")
            filename = _safe_source(supplied.get("filename"))
            if "/" in filename:
                raise ContentPackError("Quick Card asset filenames cannot contain folders")
            if not filename.lower().endswith(".bmp"):
                raise ContentPackError("Quick Card assets must be BMP files")
            encoded = str(supplied.get("data_base64") or "")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as err:
                raise ContentPackError(f"{filename} is not valid base64") from err
            if not content.startswith(b"BM"):
                raise ContentPackError(f"{filename} is not a BMP file")
            source = f"quick-cards/assets/{filename}"
            files[source] = content
            manifest_files.append(
                {
                    "source": source,
                    "target": f"/cards/flexdisplay/assets/{filename}",
                }
            )

        descriptor = {
            "version": version,
            "name": str(payload.get("name") or version)[:120],
            "description": str(payload.get("description") or "Offline Quick Cards")[:500],
            "kind": "quick_cards",
            "files": manifest_files,
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("content-pack.json", json.dumps(descriptor))
            for source, content in files.items():
                bundle.writestr(source, content)
        record = self.install(archive.getvalue())
        with self._lock:
            record = self._state["packs"][version]
            record["kind"] = "quick_cards"
            record["card_count"] = len(normalised_cards)
            self._save()
            return deepcopy(record)

    def assign(
        self,
        version: str,
        device_ids: list[str],
        *,
        scope: str = "devices",
        scheduled_for: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if version not in self._state["packs"]:
                raise ContentPackError("Unknown content pack")
            scheduled = scheduled_for.strip()
            if scheduled:
                try:
                    parsed = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    scheduled = parsed.astimezone(UTC).isoformat(timespec="seconds")
                except ValueError as err:
                    raise ContentPackError("Scheduled time must be an ISO-8601 date") from err
            deployment = {
                "id": hashlib.sha256(
                    f"{version}:{','.join(sorted(device_ids))}:{now()}".encode()
                ).hexdigest()[:16],
                "version": version,
                "scope": scope,
                "device_ids": list(device_ids),
                "scheduled_for": scheduled or None,
                "created_at": now(),
            }
            for device_id in device_ids:
                self._state["assignments"][device_id] = {
                    "desired_version": version,
                    "status": "scheduled" if scheduled else "pending",
                    "error": "",
                    "deployment_id": deployment["id"],
                    "scheduled_for": scheduled or None,
                    "assigned_at": now(),
                    "updated_at": now(),
                }
            self._state["deployments"].append(deployment)
            self._state["deployments"] = self._state["deployments"][-50:]
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
            scheduled_for = str(assignment.get("scheduled_for") or "")
            if scheduled_for:
                try:
                    if datetime.fromisoformat(scheduled_for) > datetime.now(UTC):
                        return None
                except ValueError:
                    pass
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
