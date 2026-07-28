from __future__ import annotations

import hashlib
import io
import re
import threading
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BADGE_PHOTO_BYTES = 5 * 1024 * 1024
MAX_BADGE_PHOTO_PIXELS = 20_000_000
ASSET_PATTERN = re.compile(r"^[a-f0-9]{24}$")
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP"}


class DashboardAssetValidationError(ValueError):
    """Raised when a Dashboard Studio asset is not safe to persist."""


def _bounded_filename(value: Any) -> str:
    selected = str(value or "profile-photo")
    selected = selected.replace("\r", " ").replace("\n", " ").strip()
    return selected[:160] or "profile-photo"


class DashboardAssetStore:
    """Persist normalized profile photos used by standalone dashboard cards."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def put_profile_photo(
        self,
        content: bytes,
        filename: str,
    ) -> dict[str, Any]:
        if not content:
            raise DashboardAssetValidationError("The profile photo is empty")
        if len(content) > MAX_BADGE_PHOTO_BYTES:
            raise DashboardAssetValidationError("Profile photos may not exceed 5 MB")
        try:
            with Image.open(io.BytesIO(content)) as opened:
                image_format = str(opened.format or "").upper()
                if image_format not in SUPPORTED_FORMATS:
                    raise DashboardAssetValidationError(
                        "Profile photos must be JPEG, PNG, WebP, or BMP images"
                    )
                width, height = opened.size
                if (
                    width <= 0
                    or height <= 0
                    or width * height > MAX_BADGE_PHOTO_PIXELS
                ):
                    raise DashboardAssetValidationError(
                        "Profile photo dimensions exceed the 20 megapixel limit"
                    )
                opened.load()
                source = ImageOps.exif_transpose(opened).convert("RGBA")
        except DashboardAssetValidationError:
            raise
        except (UnidentifiedImageError, OSError) as err:
            raise DashboardAssetValidationError(
                "The upload is not a readable profile photo"
            ) from err

        background = Image.new("RGBA", source.size, "white")
        background.alpha_composite(source)
        normalized = background.convert("RGB")
        normalized.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        normalized.save(output, format="PNG", optimize=True)
        encoded = output.getvalue()
        digest = hashlib.sha256(encoded).hexdigest()[:24]
        with self._lock:
            self.path.mkdir(parents=True, exist_ok=True)
            destination = self.path / f"{digest}.png"
            if not destination.exists():
                temporary = self.path / f".{digest}.tmp"
                temporary.write_bytes(encoded)
                temporary.replace(destination)
        return {
            "id": digest,
            "filename": _bounded_filename(filename),
            "width": normalized.width,
            "height": normalized.height,
            "size": len(encoded),
        }

    def profile_photo(self, asset_id: str) -> bytes:
        selected = str(asset_id or "")
        if not ASSET_PATTERN.fullmatch(selected):
            return b""
        path = self.path / f"{selected}.png"
        try:
            return path.read_bytes() if path.is_file() else b""
        except OSError:
            return b""
