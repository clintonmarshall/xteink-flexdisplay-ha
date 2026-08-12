from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import json
import re
import secrets
import shutil
import threading
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from PIL import Image, UnidentifiedImageError


MAX_PACK_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_DESCRIPTOR_BYTES = 256 * 1024
MAX_FILES = 64
MAX_CARDS = 32
MAX_CARD_IMAGE_WIDTH = 1200
MAX_CARD_IMAGE_HEIGHT = 1600
MAX_QUICK_CARD_REQUEST_BYTES = 48 * 1024 * 1024
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DOWNLOAD_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
QUICK_CARD_ASSET_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,115}\.bmp$", re.IGNORECASE
)
MANAGED_PREFIXES = (
    "/factory-content/",
    "/photos/flexdisplay/",
    "/books/flexdisplay/",
    "/cards/flexdisplay/",
    "/.crosspoint/fleet/",
)


class ContentPackError(ValueError):
    pass


class ContentPackConflictError(ContentPackError):
    """Raised when an immutable content-pack version already exists."""


class ContentPackAccessError(ContentPackError):
    """Raised when a device download does not present the pack access token."""


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
        changed = False
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._state = {
                    "packs": loaded.get("packs") or {},
                    "assignments": loaded.get("assignments") or {},
                    "deployments": loaded.get("deployments") or [],
                }
                for record in self._state["packs"].values():
                    if isinstance(record, dict) and not record.get("download_token"):
                        record["download_token"] = secrets.token_urlsafe(32)
                        changed = True
        except (OSError, json.JSONDecodeError):
            pass
        if changed:
            self._save()

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
            payload = deepcopy(self._state)
            for record in payload["packs"].values():
                if isinstance(record, dict):
                    record.pop("download_token", None)
            return payload

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
        if len(names) != len(set(names)):
            raise ContentPackError("Content pack ZIP paths must be unique")
        descriptor_info = bundle.getinfo("content-pack.json")
        if descriptor_info.is_dir() or descriptor_info.file_size > MAX_DESCRIPTOR_BYTES:
            raise ContentPackError("content-pack.json is too large")
        try:
            supplied = json.loads(bundle.read("content-pack.json"))
        except (
            KeyError,
            UnicodeDecodeError,
            ValueError,
            RecursionError,
            zipfile.BadZipFile,
            RuntimeError,
        ) as err:
            raise ContentPackError("content-pack.json is invalid") from err
        if not isinstance(supplied, dict):
            raise ContentPackError("content-pack.json must contain a JSON object")
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
            try:
                content = bundle.read(source)
            except (KeyError, zipfile.BadZipFile, RuntimeError) as err:
                raise ContentPackError(f"{source} could not be read safely") from err
            total += len(content)
            if total > MAX_PACK_BYTES:
                raise ContentPackError("Expanded content pack is too large")
            files.append(
                {
                    "source": source,
                    "target": target,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "_content": content,
                }
            )

        record = {
            "version": version,
            "name": str(supplied.get("name") or version)[:120],
            "description": str(supplied.get("description") or "")[:500],
            "kind": str(supplied.get("kind") or "assets")[:32],
            "created_at": now(),
            "file_count": len(files),
            "size": total,
            "files": [
                {key: value for key, value in item.items() if key != "_content"}
                for item in files
            ],
            "download_token": secrets.token_urlsafe(32),
        }
        if record["kind"] == "quick_cards":
            supplied_card_count = supplied.get("card_count")
            record["card_count"] = (
                max(0, min(MAX_CARDS, supplied_card_count))
                if isinstance(supplied_card_count, int)
                else 0
            )
        with self._lock:
            destination = self.content_root / version
            if version in self._state["packs"] or destination.exists():
                raise ContentPackConflictError(
                    f"Content pack version {version} already exists; use a new version"
                )
            temporary = self.content_root / f".{version}.tmp"
            if temporary.exists():
                shutil.rmtree(temporary)
            try:
                temporary.mkdir(parents=True)
                for item in files:
                    output = temporary / item["source"]
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(item["_content"])
                temporary.replace(destination)
            except OSError as err:
                if temporary.exists():
                    shutil.rmtree(temporary)
                raise ContentPackError(
                    "Content pack files could not be stored safely"
                ) from err
            self._state["packs"][version] = record
            try:
                self._save()
            except OSError as err:
                self._state["packs"].pop(version, None)
                if destination.exists():
                    shutil.rmtree(destination)
                raise ContentPackError(
                    "Content pack state could not be stored safely"
                ) from err
            public_record = deepcopy(record)
            public_record.pop("download_token", None)
            return public_record

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
        if len(assets) > MAX_FILES - 1:
            raise ContentPackError(
                f"Quick Card packs support at most {MAX_FILES - 1} assets"
            )
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
        asset_targets: set[str] = set()
        asset_names: set[str] = set()
        total_size = len(files["quick-cards/cards.json"])
        for supplied in assets:
            if not isinstance(supplied, dict):
                raise ContentPackError("Every Quick Card asset must be an object")
            filename = _safe_source(supplied.get("filename"))
            if not QUICK_CARD_ASSET_PATTERN.fullmatch(filename):
                raise ContentPackError(
                    "Quick Card asset names must be safe BMP basenames up to 120 characters"
                )
            normalised_filename = filename.casefold()
            if normalised_filename in asset_names:
                raise ContentPackError(f"Duplicate Quick Card asset: {filename}")
            encoded = str(supplied.get("data_base64") or "")
            if len(encoded) > ((MAX_FILE_BYTES + 2) // 3) * 4:
                raise ContentPackError(f"{filename} is too large")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as err:
                raise ContentPackError(f"{filename} is not valid base64") from err
            if len(content) > MAX_FILE_BYTES:
                raise ContentPackError(f"{filename} is too large")
            total_size += len(content)
            if total_size > MAX_PACK_BYTES:
                raise ContentPackError("Expanded Quick Card pack is too large")
            try:
                with Image.open(io.BytesIO(content)) as image:
                    width, height = image.size
                    image_format = image.format
                    image_mode = image.mode
                    if image_format != "BMP" or image_mode != "1":
                        raise ContentPackError(
                            f"{filename} must be a one-bit BMP image"
                        )
                    if (
                        width < 1
                        or height < 1
                        or width > MAX_CARD_IMAGE_WIDTH
                        or height > MAX_CARD_IMAGE_HEIGHT
                    ):
                        raise ContentPackError(
                            f"{filename} dimensions must be between 1 x 1 and "
                            f"{MAX_CARD_IMAGE_WIDTH} x {MAX_CARD_IMAGE_HEIGHT}"
                        )
                    image.load()
            except ContentPackError:
                raise
            except (
                Image.DecompressionBombError,
                UnidentifiedImageError,
                OSError,
                ValueError,
            ) as err:
                raise ContentPackError(f"{filename} is not a valid BMP image") from err
            source = f"quick-cards/assets/{filename}"
            target = f"/cards/flexdisplay/assets/{filename}"
            files[source] = content
            asset_names.add(normalised_filename)
            asset_targets.add(target)
            manifest_files.append(
                {
                    "source": source,
                    "target": target,
                }
            )

        for card in normalised_cards:
            image_path = str(card.get("image_path") or "")
            if card["type"] == "image" and not image_path:
                raise ContentPackError(
                    f"Quick Card {card['id']} requires an included image"
                )
            if image_path and image_path not in asset_targets:
                raise ContentPackError(
                    f"Quick Card {card['id']} image is not included in this pack"
                )

        descriptor = {
            "version": version,
            "name": str(payload.get("name") or version)[:120],
            "description": str(payload.get("description") or "Offline Quick Cards")[:500],
            "kind": "quick_cards",
            "card_count": len(normalised_cards),
            "files": manifest_files,
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("content-pack.json", json.dumps(descriptor))
            for source, content in files.items():
                bundle.writestr(source, content)
        return self.install(archive.getvalue())

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
            created_at = datetime.now(UTC).isoformat(timespec="microseconds")
            deployment = {
                "id": hashlib.sha256(
                    (
                        f"{version}:{scope}:{scheduled}:"
                        f"{','.join(sorted(device_ids))}:{created_at}"
                    ).encode()
                ).hexdigest()[:16],
                "version": version,
                "scope": scope,
                "device_ids": list(device_ids),
                "scheduled_for": scheduled or None,
                "created_at": created_at,
            }
            for device_id in device_ids:
                self._state["assignments"][device_id] = {
                    "desired_version": version,
                    "status": "scheduled" if scheduled else "pending",
                    "error": "",
                    "deployment_id": deployment["id"],
                    "scheduled_for": scheduled or None,
                    "assigned_at": created_at,
                    "updated_at": created_at,
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

    def download_token(self, version: str) -> str:
        """Return the opaque device-download token without exposing it in API state."""
        with self._lock:
            pack = self._state["packs"].get(version)
            if not pack:
                raise ContentPackError("Unknown content pack")
            return str(pack.get("download_token") or "")

    def _authorized_pack(self, version: str, access_token: str) -> dict[str, Any]:
        pack = self._state["packs"].get(version)
        expected = str((pack or {}).get("download_token") or "")
        if (
            not pack
            or not DOWNLOAD_TOKEN_PATTERN.fullmatch(access_token)
            or not hmac.compare_digest(expected.encode("ascii"), access_token.encode("ascii"))
        ):
            raise ContentPackAccessError("Unknown content pack download")
        return pack

    def manifest(
        self, version: str, base_url: str, access_token: str
    ) -> tuple[bytes, str]:
        with self._lock:
            pack = self._authorized_pack(version, access_token)
            files = deepcopy(pack["files"])
        prefix = base_url.rstrip("/")
        for item in files:
            encoded_source = quote(str(item["source"]), safe="/")
            item["url"] = (
                f"{prefix}/api/v1/content-packs/{version}/files/{encoded_source}"
                f"?access_token={access_token}"
            )
        content = json.dumps(
            {"format": 1, "version": version, "files": files},
            separators=(",", ":"),
        ).encode()
        return content, hashlib.sha256(content).hexdigest()

    def file_path(self, version: str, source: str, access_token: str) -> Path:
        selected = _safe_source(source)
        with self._lock:
            pack = self._authorized_pack(version, access_token)
            if selected not in {item["source"] for item in pack["files"]}:
                raise ContentPackError("Unknown content pack file")
        path = (self.content_root / version / selected).resolve()
        if not path.is_relative_to((self.content_root / version).resolve()):
            raise ContentPackError("Unsafe content pack file")
        return path
