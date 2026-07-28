from __future__ import annotations

import hashlib
import io
import json
import threading
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

MAX_LOGO_BYTES = 2 * 1024 * 1024
LAYOUTS = {"centered", "logo", "identity", "minimal"}
POLICIES = {"always", "manual", "usb", "never"}

DEFAULT_LOADING_SCREEN: dict[str, Any] = {
    "enabled": True,
    "policy": "always",
    "layout": "centered",
    "headline": "Updating dashboard",
    "message": "Fetching the latest information",
    "owner_name": "",
    "show_device_name": True,
    "show_owner": False,
    "show_area": False,
    "logo_sha256": "",
    "logo_filename": "",
}


class LoadingScreenValidationError(ValueError):
    """Raised when a loading-screen design cannot be safely stored."""


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _bounded_text(value: Any, fallback: str, maximum: int) -> str:
    selected = str(value if value is not None else fallback)
    selected = selected.replace("\r", " ").replace("\n", " ").strip()
    return selected[:maximum]


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    maximum_width: int,
    start_size: int,
    minimum_size: int,
    *,
    bold: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for size in range(start_size, minimum_size - 1, -1):
        font = _font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= maximum_width:
            return font
    return _font(minimum_size, bold)


def _centered_text(
    draw: ImageDraw.ImageDraw,
    y: int,
    text: str,
    width: int,
    start_size: int,
    minimum_size: int,
    *,
    bold: bool = False,
) -> int:
    if not text:
        return y
    font = _fit_font(draw, text, width - 48, start_size, minimum_size, bold=bold)
    box = draw.textbbox((0, 0), text, font=font)
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    draw.text(((width - text_width) // 2, y - box[1]), text, font=font, fill=0)
    return y + text_height


def _draw_sync_icon(draw: ImageDraw.ImageDraw, width: int, top: int, size: int) -> None:
    left = (width - size) // 2
    right = left + size
    bottom = top + size
    stroke = max(4, size // 18)
    draw.arc((left, top, right, bottom), 205, 345, fill=0, width=stroke)
    draw.arc((left, top, right, bottom), 25, 165, fill=0, width=stroke)
    draw.polygon(
        [
            (right - size // 12, top + size // 3),
            (right - size // 12, top + size // 12),
            (right - size // 3, top + size // 12),
        ],
        fill=0,
    )
    draw.polygon(
        [
            (left + size // 12, bottom - size // 3),
            (left + size // 12, bottom - size // 12),
            (left + size // 3, bottom - size // 12),
        ],
        fill=0,
    )


def _replace_tokens(value: str, context: dict[str, str]) -> str:
    rendered = value
    for name, replacement in context.items():
        rendered = rendered.replace(f"{{{name}}}", replacement)
    return rendered


def _validate(payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    selected = {**DEFAULT_LOADING_SCREEN, **fallback}
    selected.update(
        {
            "enabled": bool(payload.get("enabled", selected["enabled"])),
            "policy": str(payload.get("policy") or selected["policy"]).lower(),
            "layout": str(payload.get("layout") or selected["layout"]).lower(),
            "headline": _bounded_text(payload.get("headline"), selected["headline"], 80),
            "message": _bounded_text(payload.get("message"), selected["message"], 120),
            "owner_name": _bounded_text(payload.get("owner_name"), selected["owner_name"], 80),
            "show_device_name": bool(
                payload.get("show_device_name", selected["show_device_name"])
            ),
            "show_owner": bool(payload.get("show_owner", selected["show_owner"])),
            "show_area": bool(payload.get("show_area", selected["show_area"])),
        }
    )
    if selected["policy"] not in POLICIES:
        raise LoadingScreenValidationError("Unsupported loading-screen display policy")
    if selected["layout"] not in LAYOUTS:
        raise LoadingScreenValidationError("Unsupported loading-screen layout")
    if not selected["headline"] and not selected["message"]:
        raise LoadingScreenValidationError("Add a headline or message")
    return selected


class LoadingScreenStore:
    """Persist and render fleet-default and per-device fetch screens."""

    def __init__(self, path: Path):
        self.path = path
        self.asset_dir = path.with_name("loading-screen-assets")
        self._lock = threading.RLock()
        self._default = dict(DEFAULT_LOADING_SCREEN)
        self._devices: dict[str, dict[str, Any]] = {}
        self._render_cache: dict[str, tuple[bytes, str]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            default_raw = raw.get("default")
            if isinstance(default_raw, dict):
                self._default = _validate(default_raw, DEFAULT_LOADING_SCREEN)
                self._default["logo_sha256"] = _bounded_text(
                    default_raw.get("logo_sha256"), "", 64
                )
                self._default["logo_filename"] = _bounded_text(
                    default_raw.get("logo_filename"), "", 160
                )
            devices_raw = raw.get("devices")
            if isinstance(devices_raw, dict):
                for device_id, value in devices_raw.items():
                    if not isinstance(value, dict):
                        continue
                    parsed = _validate(value, self._default)
                    parsed["logo_sha256"] = _bounded_text(
                        value.get("logo_sha256"), "", 64
                    )
                    parsed["logo_filename"] = _bounded_text(
                        value.get("logo_filename"), "", 160
                    )
                    self._devices[str(device_id)] = parsed
        except (OSError, json.JSONDecodeError, LoadingScreenValidationError):
            return

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"default": self._default, "devices": self._devices},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "default": dict(self._default),
                "devices": {
                    device_id: dict(value)
                    for device_id, value in self._devices.items()
                },
                "capabilities": {
                    "layouts": sorted(LAYOUTS),
                    "policies": sorted(POLICIES),
                    "maximum_logo_bytes": MAX_LOGO_BYTES,
                    "tokens": [
                        "device_name",
                        "device_id",
                        "owner",
                        "area",
                        "profile",
                    ],
                },
            }

    def effective(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            selected = self._devices.get(device_id)
            result = dict(selected if selected is not None else self._default)
            result["inherited"] = selected is None
            return result

    def put(self, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self._default if target == "default" else self._devices.get(
                target, self._default
            )
            parsed = _validate(payload, current)
            parsed["logo_sha256"] = str(current.get("logo_sha256") or "")
            parsed["logo_filename"] = str(current.get("logo_filename") or "")
            if target == "default":
                self._default = parsed
            else:
                self._devices[target] = parsed
            self._render_cache.clear()
            self._save()
            return self.effective(target)

    def reset(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            self._devices.pop(device_id, None)
            self._render_cache.clear()
            self._save()
            return self.effective(device_id)

    def _logo_path(self, target: str, config: dict[str, Any]) -> Path | None:
        digest = str(config.get("logo_sha256") or "")
        if not digest:
            return None
        path = self.asset_dir / f"{target}-{digest}.png"
        if not path.exists() and target != "default":
            path = self.asset_dir / f"default-{digest}.png"
        return path if path.exists() else None

    def put_logo(self, target: str, content: bytes, filename: str) -> dict[str, Any]:
        if not content or len(content) > MAX_LOGO_BYTES:
            raise LoadingScreenValidationError("Logo must be between 1 byte and 2 MB")
        try:
            with Image.open(io.BytesIO(content)) as source:
                if source.width * source.height > 12_000_000:
                    raise LoadingScreenValidationError("Logo image is too large")
                normalized = source.convert("RGBA")
                normalized.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                normalized.save(output, format="PNG", optimize=True)
                encoded = output.getvalue()
        except (UnidentifiedImageError, OSError) as err:
            raise LoadingScreenValidationError(
                "Logo must be a JPEG, PNG, WebP, or BMP image"
            ) from err

        digest = hashlib.sha256(encoded).hexdigest()
        with self._lock:
            self.asset_dir.mkdir(parents=True, exist_ok=True)
            (self.asset_dir / f"{target}-{digest}.png").write_bytes(encoded)
            current = dict(
                self._default
                if target == "default"
                else self._devices.get(target, self._default)
            )
            current["logo_sha256"] = digest
            current["logo_filename"] = _bounded_text(filename, "logo", 160)
            if target == "default":
                self._default = current
            else:
                self._devices[target] = current
            self._render_cache.clear()
            self._save()
            return self.effective(target)

    def clear_logo(self, target: str) -> dict[str, Any]:
        with self._lock:
            current = dict(
                self._default
                if target == "default"
                else self._devices.get(target, self._default)
            )
            current["logo_sha256"] = ""
            current["logo_filename"] = ""
            if target == "default":
                self._default = current
            else:
                self._devices[target] = current
            self._render_cache.clear()
            self._save()
            return self.effective(target)

    def render(
        self,
        device_id: str,
        device: dict[str, Any],
        width: int,
        height: int,
        *,
        config_override: dict[str, Any] | None = None,
        target_override: str | None = None,
    ) -> tuple[bytes, str]:
        config = (
            _validate(config_override, self.effective(device_id))
            if config_override is not None
            else self.effective(device_id)
        )
        if config_override is not None:
            effective = self.effective(device_id)
            config["logo_sha256"] = effective.get("logo_sha256", "")
            config["logo_filename"] = effective.get("logo_filename", "")
        target = target_override or (
            device_id if not config.get("inherited", False) else "default"
        )
        context = {
            "device_name": str(device.get("name") or device_id),
            "device_id": device_id,
            "owner": str(config.get("owner_name") or ""),
            "area": str(device.get("area") or ""),
            "profile": str(device.get("profile") or ""),
        }
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "device_id": device_id,
                    "device": context,
                    "width": width,
                    "height": height,
                    "target": target,
                    "config": config,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            cached = self._render_cache.get(cache_key)
        if cached is not None:
            return cached
        headline = _replace_tokens(str(config.get("headline") or ""), context)
        message = _replace_tokens(str(config.get("message") or ""), context)

        image = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(image)
        inset = max(18, width // 20)
        draw.rounded_rectangle(
            (inset, inset, width - inset, height - inset),
            radius=max(12, width // 28),
            outline=0,
            width=max(2, width // 180),
        )

        logo_path = self._logo_path(target, config)
        layout = str(config.get("layout") or "centered")
        logo_top = height // 9
        logo_height = height // (3 if layout == "logo" else 5)
        logo_drawn = False
        if logo_path:
            with Image.open(logo_path) as source:
                rgba = source.convert("RGBA")
                contained = ImageOps.contain(
                    rgba,
                    (width - inset * 4, logo_height),
                    Image.Resampling.LANCZOS,
                )
                background = Image.new("RGBA", contained.size, "white")
                background.alpha_composite(contained)
                logo = ImageOps.autocontrast(background.convert("L"))
                left = (width - logo.width) // 2
                image.paste(logo, (left, logo_top))
                logo_drawn = True
                content_y = logo_top + logo.height + max(18, height // 40)
        else:
            icon_size = min(width // 4, height // 7)
            _draw_sync_icon(draw, width, logo_top, icon_size)
            content_y = logo_top + icon_size + max(18, height // 36)

        if layout == "minimal":
            content_y = height // 2
        elif layout == "identity":
            content_y = max(content_y, height // 3)

        content_y = _centered_text(
            draw,
            content_y,
            headline,
            width,
            42 if layout != "minimal" else 32,
            22,
            bold=True,
        )
        content_y += max(12, height // 55)
        content_y = _centered_text(draw, content_y, message, width, 25, 16)

        identity_lines: list[str] = []
        if config.get("show_device_name"):
            identity_lines.append(context["device_name"])
        if config.get("show_owner") and context["owner"]:
            identity_lines.append(f"Owner: {context['owner']}")
        if config.get("show_area") and context["area"]:
            identity_lines.append(context["area"])
        if identity_lines:
            identity_y = max(content_y + height // 16, height * 3 // 4)
            if layout == "identity":
                identity_y = max(content_y + height // 12, height * 2 // 3)
            for index, line in enumerate(identity_lines):
                identity_y = _centered_text(
                    draw,
                    identity_y,
                    line,
                    width,
                    30 if index == 0 else 20,
                    15,
                    bold=index == 0,
                )
                identity_y += 8

        dot_y = height - inset - max(24, height // 28)
        dot_size = max(5, width // 90)
        gap = dot_size * 3
        for offset in (-gap, 0, gap):
            draw.ellipse(
                (
                    width // 2 + offset - dot_size,
                    dot_y - dot_size,
                    width // 2 + offset + dot_size,
                    dot_y + dot_size,
                ),
                fill=0 if offset == 0 else 255,
                outline=0,
                width=2,
            )

        if logo_drawn:
            draw.text(
                (inset + 12, height - inset - 24),
                "FLEXDISPLAY",
                font=_font(11, True),
                fill=0,
            )

        monochrome = image.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        output = io.BytesIO()
        monochrome.save(output, format="BMP")
        content = output.getvalue()
        result = (content, hashlib.sha256(content).hexdigest())
        with self._lock:
            self._render_cache[cache_key] = result
            if len(self._render_cache) > 64:
                self._render_cache.pop(next(iter(self._render_cache)))
        return result
