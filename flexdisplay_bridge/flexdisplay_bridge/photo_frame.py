from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import UTC, datetime, time, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .eink_calibration import calibrate_monochrome, normalize_model

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ALBUM_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
IMAGE_PATTERN = re.compile(r"^[a-f0-9]{16}$")
IMAGE_FORMATS = {
    "JPEG": ("jpeg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "BMP": ("bmp", "image/bmp"),
}
IMAGE_FITS = {"cover", "contain"}
ROTATIONS = {0, 90, 180, 270}


class PhotoFrameValidationError(ValueError):
    """Raised when a Photo Frame album or media item is unsafe to persist."""


def _bounded_text(value: Any, fallback: str, maximum: int) -> str:
    selected = str(value or fallback).replace("\r", " ").replace("\n", " ").strip()
    return selected[:maximum] or fallback


def _integer(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return fallback


def _clock(value: Any, fallback: str) -> str:
    candidate = str(value or fallback)
    try:
        hour, minute = (int(part) for part in candidate.split(":", 1))
    except (TypeError, ValueError):
        return fallback
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return fallback
    return f"{hour:02d}:{minute:02d}"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    )
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def _image_from_bytes(content: bytes) -> tuple[Image.Image, str, str]:
    if not content:
        raise PhotoFrameValidationError("The uploaded image is empty")
    if len(content) > MAX_IMAGE_BYTES:
        raise PhotoFrameValidationError("Images may not exceed 8 MB")
    try:
        with Image.open(BytesIO(content)) as opened:
            image_format = str(opened.format or "").upper()
            if image_format not in IMAGE_FORMATS:
                raise PhotoFrameValidationError(
                    "Photo Frame accepts JPEG, PNG, WebP, or BMP images"
                )
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise PhotoFrameValidationError("Image dimensions exceed the 20 megapixel limit")
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGBA")
    except PhotoFrameValidationError:
        raise
    except (UnidentifiedImageError, OSError) as err:
        raise PhotoFrameValidationError("The upload is not a readable image") from err
    extension, media_type = IMAGE_FORMATS[image_format]
    return image, extension, media_type


def _fit_caption(draw: ImageDraw.ImageDraw, caption: str, width: int, start: int) -> ImageFont.ImageFont:
    for size in range(start, 11, -1):
        selected = _font(size, True)
        if draw.textbbox((0, 0), caption, font=selected)[2] <= width:
            return selected
    return _font(11, True)


def render_eink(
    content: bytes,
    *,
    width: int,
    height: int,
    fit: str = "cover",
    rotation: int = 0,
    caption: str = "",
    output_format: str = "PNG",
) -> bytes:
    """Render a source image exactly as a monochrome X3/X4 Photo Frame."""
    if not 240 <= width <= 1200 or not 240 <= height <= 1600:
        raise PhotoFrameValidationError("Preview dimensions are outside the supported range")
    if fit not in IMAGE_FITS:
        raise PhotoFrameValidationError("Image fit must be cover or contain")
    if rotation not in ROTATIONS:
        raise PhotoFrameValidationError("Rotation must be 0, 90, 180, or 270 degrees")

    source, _, _ = _image_from_bytes(content)
    if rotation:
        source = source.rotate(-rotation, expand=True)
    background = Image.new("RGBA", source.size, "white")
    background.alpha_composite(source)
    normalized = ImageOps.autocontrast(background.convert("L"))

    caption = _bounded_text(caption, "", 120)
    caption_height = max(58, height // 11) if caption else 0
    target = (width, height - caption_height)
    if fit == "contain":
        stage = Image.new("L", target, 255)
        fitted = ImageOps.contain(normalized, target, Image.Resampling.LANCZOS)
        stage.paste(
            fitted,
            ((target[0] - fitted.width) // 2, (target[1] - fitted.height) // 2),
        )
    else:
        stage = ImageOps.fit(
            normalized,
            target,
            Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    canvas = Image.new("L", (width, height), 255)
    canvas.paste(stage, (0, 0))
    if caption:
        draw = ImageDraw.Draw(canvas)
        top = height - caption_height
        draw.line((0, top, width, top), fill=0, width=max(2, width // 240))
        font = _fit_caption(draw, caption, width - 28, max(18, height // 27))
        box = draw.textbbox((0, 0), caption, font=font)
        draw.text(
            ((width - (box[2] - box[0])) // 2, top + (caption_height - (box[3] - box[1])) // 2 - box[1]),
            caption,
            fill=0,
            font=font,
        )

    rendered = calibrate_monochrome(
        canvas,
        model=normalize_model(None, width, height),
        photo=True,
    )
    output = BytesIO()
    rendered.save(output, format=output_format)
    return output.getvalue()


def render_empty_album(
    album_name: str,
    width: int,
    height: int,
    *,
    output_format: str = "BMP",
) -> bytes:
    """Render a useful device screen instead of returning an HTTP error."""
    canvas = Image.new("1", (width, height), 1)
    draw = ImageDraw.Draw(canvas)
    margin = max(24, width // 12)
    draw.rounded_rectangle(
        (margin, margin, width - margin, height - margin),
        radius=max(16, width // 24),
        outline=0,
        width=max(3, width // 160),
    )
    title_font = _font(max(28, width // 11), True)
    message_font = _font(max(20, width // 18), True)
    detail_font = _font(max(16, width // 25))
    title = "PHOTO FRAME"
    name = _bounded_text(album_name, "Album", 48)
    for text_value, font, y in (
        (title, title_font, height * 0.27),
        (name, message_font, height * 0.43),
        ("No photos yet", message_font, height * 0.54),
        ("Add images in FlexDisplay Studio", detail_font, height * 0.67),
    ):
        box = draw.textbbox((0, 0), text_value, font=font)
        draw.text(
            ((width - (box[2] - box[0])) // 2, int(y) - (box[3] - box[1]) // 2),
            text_value,
            fill=0,
            font=font,
        )
    output = BytesIO()
    canvas.save(output, format=output_format)
    return output.getvalue()


class PhotoFrameMediaStore:
    """Persistent albums, source images, playback state, and e-paper conversion."""

    def __init__(self, path: Path):
        self.path = path
        self.media_path = path.with_name(f"{path.stem}-media")
        self._lock = threading.RLock()
        self._data = self._load()

    @staticmethod
    def _default_album() -> dict[str, Any]:
        return {
            "name": "Default album",
            "shuffle": False,
            "interval_seconds": 3600,
            "start": "00:00",
            "end": "00:00",
            "timezone": "Australia/Melbourne",
            "items": [],
        }

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload.setdefault("version", 1)
                    payload.setdefault("albums", {})
                    payload.setdefault("assignments", {})
                    payload.setdefault("playback", {})
                    payload["assignments"] = {
                        device_id: album_id
                        for device_id, album_id in payload["assignments"].items()
                        if device_id and device_id.upper() != "UNKNOWN"
                    }
                    payload["playback"] = {
                        device_id: state
                        for device_id, state in payload["playback"].items()
                        if device_id and device_id.upper() != "UNKNOWN"
                    }
                    if "default" not in payload["albums"]:
                        payload["albums"]["default"] = self._default_album()
                    return payload
            except (OSError, ValueError):
                pass
        return {
            "version": 1,
            "albums": {"default": self._default_album()},
            "assignments": {},
            "playback": {},
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.media_path.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(self._data, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def payload(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def put_album(self, album_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not ALBUM_PATTERN.fullmatch(album_id):
            raise PhotoFrameValidationError(
                "Album IDs may contain only letters, numbers, underscores, and hyphens"
            )
        with self._lock:
            current = self._data["albums"].get(album_id, self._default_album())
            timezone = _bounded_text(payload.get("timezone"), current["timezone"], 64)
            try:
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError as err:
                raise PhotoFrameValidationError("Album timezone is not recognised") from err
            album = {
                "name": _bounded_text(payload.get("name"), current["name"], 64),
                "shuffle": bool(payload.get("shuffle", current["shuffle"])),
                "interval_seconds": _integer(
                    payload.get("interval_seconds"),
                    current["interval_seconds"],
                    30,
                    86400,
                ),
                "start": _clock(payload.get("start"), current["start"]),
                "end": _clock(payload.get("end"), current["end"]),
                "timezone": timezone,
                "items": current.get("items", []),
            }
            self._data["albums"][album_id] = album
            self._save()
            return json.loads(json.dumps(album))

    def delete_album(self, album_id: str) -> None:
        with self._lock:
            if album_id == "default":
                raise PhotoFrameValidationError("The default album cannot be deleted")
            album = self._data["albums"].pop(album_id, None)
            if album is None:
                raise KeyError(album_id)
            for item in album.get("items", []):
                self._remove_source(item)
            self._data["assignments"] = {
                device: selected
                for device, selected in self._data["assignments"].items()
                if selected != album_id
            }
            self._data["playback"] = {
                device: state
                for device, state in self._data["playback"].items()
                if state.get("album_id") != album_id
            }
            self._save()

    def add_image(
        self,
        album_id: str,
        content: bytes,
        *,
        filename: str,
        caption: str = "",
        fit: str = "cover",
        rotation: int = 0,
        source: str = "upload",
    ) -> dict[str, Any]:
        image, extension, media_type = _image_from_bytes(content)
        if fit not in IMAGE_FITS:
            raise PhotoFrameValidationError("Image fit must be cover or contain")
        if rotation not in ROTATIONS:
            raise PhotoFrameValidationError("Rotation must be 0, 90, 180, or 270 degrees")
        with self._lock:
            album = self._data["albums"].get(album_id)
            if not album:
                raise KeyError(album_id)
            item_id = uuid.uuid4().hex[:16]
            source_path = self.media_path / f"{item_id}.{extension}"
            self.media_path.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(content)
            item = {
                "id": item_id,
                "filename": _bounded_text(filename, f"{item_id}.{extension}", 160),
                "caption": _bounded_text(caption, "", 120),
                "fit": fit,
                "rotation": rotation,
                "source": _bounded_text(source, "upload", 128),
                "media_type": media_type,
                "width": image.width,
                "height": image.height,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "path": source_path.name,
                "created_at": datetime.now(UTC).isoformat(),
            }
            album["items"].append(item)
            self._save()
            return json.loads(json.dumps(item))

    def update_image(self, album_id: str, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            item = self._item(album_id, item_id)
            fit = str(payload.get("fit", item["fit"]))
            rotation = _integer(payload.get("rotation"), item["rotation"], 0, 270)
            if fit not in IMAGE_FITS:
                raise PhotoFrameValidationError("Image fit must be cover or contain")
            if rotation not in ROTATIONS:
                raise PhotoFrameValidationError("Rotation must be 0, 90, 180, or 270 degrees")
            item["caption"] = _bounded_text(payload.get("caption"), item["caption"], 120)
            item["fit"] = fit
            item["rotation"] = rotation
            self._save()
            return json.loads(json.dumps(item))

    def delete_image(self, album_id: str, item_id: str) -> None:
        with self._lock:
            album = self._data["albums"].get(album_id)
            if not album:
                raise KeyError(album_id)
            selected = next((item for item in album["items"] if item["id"] == item_id), None)
            if not selected:
                raise KeyError(item_id)
            album["items"] = [item for item in album["items"] if item["id"] != item_id]
            self._remove_source(selected)
            self._save()

    def assign(self, device_id: str, album_id: str) -> dict[str, str]:
        with self._lock:
            if album_id not in self._data["albums"]:
                raise KeyError(album_id)
            self._data["assignments"][device_id] = album_id
            self._data["playback"].pop(device_id, None)
            self._save()
            return {"device_id": device_id, "album_id": album_id}

    def render(self, album_id: str, item_id: str, width: int, height: int, output: str = "PNG") -> bytes:
        with self._lock:
            item = json.loads(json.dumps(self._item(album_id, item_id)))
            content = (self.media_path / item["path"]).read_bytes()
        return render_eink(
            content,
            width=width,
            height=height,
            fit=item["fit"],
            rotation=item["rotation"],
            caption=item["caption"],
            output_format=output,
        )

    def next_for_device(
        self,
        device_id: str,
        *,
        width: int,
        height: int,
        direction: str = "auto",
        output_format: str = "BMP",
        now: datetime | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        if direction not in {"auto", "current", "next", "previous"}:
            raise PhotoFrameValidationError("Direction must be auto, current, next, or previous")
        if output_format not in {"BMP", "PNG"}:
            raise PhotoFrameValidationError("Photo Frame output must be BMP or PNG")
        current_time = now or datetime.now(UTC)
        with self._lock:
            album_id = self._data["assignments"].get(device_id, "default")
            album = self._data["albums"].get(album_id) or self._data["albums"]["default"]
            schedule_active, next_schedule_seconds = self._schedule(album, current_time)
            if not album["items"]:
                image = render_empty_album(
                    album["name"],
                    width,
                    height,
                    output_format=output_format,
                )
                return image, {
                    "X-FlexDisplay-Photo-Album": album_id,
                    "X-FlexDisplay-Photo-ID": "",
                    "X-FlexDisplay-Photo-Filename": "No photos",
                    "X-FlexDisplay-Photo-Caption": "",
                    "X-FlexDisplay-Photo-Index": "0",
                    "X-FlexDisplay-Photo-Count": "0",
                    "X-FlexDisplay-Photo-Active": "true" if schedule_active else "false",
                    "X-FlexDisplay-Photo-Start": str(album["start"]),
                    "X-FlexDisplay-Photo-End": str(album["end"]),
                    "X-FlexDisplay-Photo-Timezone": str(album["timezone"]),
                    "X-FlexDisplay-Refresh-Interval": str(
                        album["interval_seconds"] if schedule_active else next_schedule_seconds
                    ),
                    "X-FlexDisplay-Image-SHA256": hashlib.sha256(image).hexdigest(),
                }
            playback = self._data["playback"].get(device_id, {})
            index = int(playback.get("index", 0)) % len(album["items"])
            last_advanced = self._timestamp(playback.get("last_advanced_at"))
            due = (
                last_advanced is None
                or (current_time - last_advanced).total_seconds() >= album["interval_seconds"]
            )
            should_advance = direction in {"next", "previous"} or (
                direction == "auto" and due and schedule_active
            )
            if playback.get("album_id") != album_id:
                index = 0
                should_advance = False
            elif should_advance:
                if direction == "previous":
                    index = (index - 1) % len(album["items"])
                elif album["shuffle"] and len(album["items"]) > 1:
                    bucket = int(current_time.timestamp()) // album["interval_seconds"]
                    digest = hashlib.sha256(f"{device_id}:{album_id}:{bucket}".encode()).digest()
                    next_index = int.from_bytes(digest[:4], "big") % len(album["items"])
                    index = (next_index + 1) % len(album["items"]) if next_index == index else next_index
                else:
                    index = (index + 1) % len(album["items"])
            item = album["items"][index]
            self._data["playback"][device_id] = {
                "album_id": album_id,
                "index": index,
                "last_advanced_at": (
                    current_time.isoformat()
                    if should_advance or last_advanced is None
                    else last_advanced.isoformat()
                ),
                "item_id": item["id"],
            }
            self._save()
            content = (self.media_path / item["path"]).read_bytes()
            item_copy = json.loads(json.dumps(item))

        image = render_eink(
            content,
            width=width,
            height=height,
            fit=item_copy["fit"],
            rotation=item_copy["rotation"],
            caption=item_copy["caption"],
            output_format=output_format,
        )
        return image, {
            "X-FlexDisplay-Photo-Album": album_id,
            "X-FlexDisplay-Photo-ID": item_copy["id"],
            "X-FlexDisplay-Photo-Filename": item_copy["filename"],
            "X-FlexDisplay-Photo-Caption": item_copy["caption"],
            "X-FlexDisplay-Photo-Index": str(index),
            "X-FlexDisplay-Photo-Count": str(len(album["items"])),
            "X-FlexDisplay-Photo-Active": "true" if schedule_active else "false",
            "X-FlexDisplay-Photo-Start": str(album["start"]),
            "X-FlexDisplay-Photo-End": str(album["end"]),
            "X-FlexDisplay-Photo-Timezone": str(album["timezone"]),
            "X-FlexDisplay-Refresh-Interval": str(
                album["interval_seconds"] if schedule_active else next_schedule_seconds
            ),
            "X-FlexDisplay-Image-SHA256": hashlib.sha256(image).hexdigest(),
        }

    def _item(self, album_id: str, item_id: str) -> dict[str, Any]:
        if not IMAGE_PATTERN.fullmatch(item_id):
            raise KeyError(item_id)
        album = self._data["albums"].get(album_id)
        if not album:
            raise KeyError(album_id)
        item = next((selected for selected in album["items"] if selected["id"] == item_id), None)
        if not item:
            raise KeyError(item_id)
        return item

    def _remove_source(self, item: dict[str, Any]) -> None:
        path = self.media_path / str(item.get("path") or "")
        if path.parent == self.media_path and path.exists():
            path.unlink()

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _schedule(album: dict[str, Any], now: datetime) -> tuple[bool, int]:
        try:
            zone = ZoneInfo(str(album.get("timezone") or "UTC"))
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        local = now.astimezone(zone)
        start_hour, start_minute = (
            int(part) for part in str(album.get("start") or "00:00").split(":", 1)
        )
        end_hour, end_minute = (
            int(part) for part in str(album.get("end") or "00:00").split(":", 1)
        )
        start = start_hour * 60 + start_minute
        end = end_hour * 60 + end_minute
        minute = local.hour * 60 + local.minute
        if start == end:
            return True, int(album["interval_seconds"])
        active = start <= minute < end if start < end else minute >= start or minute < end
        if active:
            return True, int(album["interval_seconds"])
        next_start = datetime.combine(
            local.date(),
            time(start_hour, start_minute),
            tzinfo=zone,
        )
        if next_start <= local:
            next_start += timedelta(days=1)
        return False, max(60, min(86400, int((next_start - local).total_seconds())))
