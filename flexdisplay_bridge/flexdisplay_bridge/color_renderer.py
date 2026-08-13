from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from io import BytesIO
from itertools import islice
from typing import Any

import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from qrcode.exceptions import DataOverflowError

from .display_profiles import DisplayProfile
from .home_assistant import EntityState

RGB = tuple[int, int, int]
ColorValue = str | tuple[int, int, int]
MAX_TILES = 4
MAX_EMBEDDED_IMAGE_BYTES = 8 * 1024 * 1024
MAX_EMBEDDED_IMAGE_PIXELS = 20_000_000

DEFAULT_THEME: dict[str, str] = {
    "background": "#07111F",
    "surface": "#10243B",
    "foreground": "#F4F8FC",
    "muted": "#93A9BD",
    "accent": "#59C3FF",
    "accent_secondary": "#76A9FF",
    "positive": "#5BDBA5",
    "warning": "#FFC857",
    "danger": "#FF6B7A",
    "grid": "#31516D",
    "shadow": "#030912",
}


class ColorRenderError(ValueError):
    """Raised when a colour preview cannot be rendered safely."""


def _parse_color(value: ColorValue, key: str) -> RGB:
    if isinstance(value, tuple) and len(value) == 3:
        if all(isinstance(part, int) and 0 <= part <= 255 for part in value):
            return value
    selected = str(value or "").removeprefix("#")
    if len(selected) == 6:
        try:
            return tuple(int(selected[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            pass
    raise ColorRenderError(f"Theme colour {key} must use six hexadecimal digits")


def normalise_color_theme(
    theme: Mapping[str, ColorValue] | None = None,
) -> dict[str, RGB]:
    selected = {**DEFAULT_THEME, **dict(theme or {})}
    return {key: _parse_color(value, key) for key, value in selected.items()}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, max(8, size))
        except OSError:
            continue
    return ImageFont.load_default()


def _safe_text(value: Any, maximum: int = 96) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()[:maximum]


def _mix(first: RGB, second: RGB, amount: float) -> RGB:
    selected = max(0.0, min(1.0, amount))
    return tuple(round(a + (b - a) * selected) for a, b in zip(first, second, strict=True))  # type: ignore[return-value]


def _fraction(entity: EntityState) -> float | None:
    try:
        value = float(entity.state)
        if not math.isfinite(value) or entity.maximum <= entity.minimum:
            return None
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, (value - entity.minimum) / (entity.maximum - entity.minimum)))


def _tile_boxes(profile: DisplayProfile, count: int) -> list[tuple[int, int, int, int]]:
    width, height = profile.resolution
    if profile.shape == "round":
        left, right = round(width * 0.16), round(width * 0.84)
        top, bottom = round(height * 0.20), round(height * 0.80)
    else:
        left, right = round(width * 0.05), round(width * 0.95)
        top, bottom = round(height * 0.10), round(height * 0.94)
    gap = max(8, min(width, height) // 40)
    if count == 1:
        return [(left, top, right, bottom)]
    if count == 2:
        middle = (top + bottom) // 2
        return [(left, top, right, middle - gap // 2), (left, middle + gap // 2, right, bottom)]
    middle_x, middle_y = (left + right) // 2, (top + bottom) // 2
    boxes = [
        (left, top, middle_x - gap // 2, middle_y - gap // 2),
        (middle_x + gap // 2, top, right, middle_y - gap // 2),
        (left, middle_y + gap // 2, middle_x - gap // 2, bottom),
        (middle_x + gap // 2, middle_y + gap // 2, right, bottom),
    ]
    return boxes[:count]


def _manifest_tile_boxes(
    profile: DisplayProfile,
    count: int,
    layout: str,
) -> list[tuple[int, int, int, int]]:
    """Mirror the receiver-owned one-to-four-card geometry for LVGL previews."""

    if profile.shape != "round":
        if count == 2 and layout == "columns":
            left, top, right, bottom = _tile_boxes(profile, 1)[0]
            gap = max(8, min(profile.resolution) // 40)
            middle = (left + right) // 2
            return [
                (left, top, middle - gap // 2, bottom),
                (middle + gap // 2, top, right, bottom),
            ]
        return _tile_boxes(profile, count)

    # The JC3636 receiver uses these exact 360x360 local coordinates. Scale the
    # same receiver-owned layout for other round hardware profiles.
    scale_x = profile.width / 360.0
    scale_y = profile.height / 360.0

    def scaled(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(  # type: ignore[return-value]
            round(value * (scale_x if index % 2 == 0 else scale_y))
            for index, value in enumerate(box)
        )

    if count <= 1:
        raw = [(29, 72, 331, 282)]
    elif count == 2 and layout == "rows":
        raw = [(29, 72, 331, 168), (29, 176, 331, 280)]
    elif count == 2:
        raw = [(29, 76, 177, 280), (183, 76, 331, 280)]
    elif count == 3:
        raw = [(29, 72, 331, 168), (29, 176, 177, 280), (183, 176, 331, 280)]
    else:
        raw = [
            (29, 72, 177, 168),
            (183, 72, 331, 168),
            (29, 176, 177, 280),
            (183, 176, 331, 280),
        ]
    return [scaled(box) for box in raw[:count]]


def _draw_text_centered(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    fill: RGB,
    *,
    bold: bool = False,
    maximum: int = 28,
) -> None:
    left, top, right, bottom = box
    selected = _safe_text(text, 64)
    available = max(1, right - left - 12)
    font = _font(maximum, bold)
    while maximum > 9 and draw.textlength(selected, font=font) > available:
        maximum -= 1
        font = _font(maximum, bold)
    while selected and draw.textlength(selected, font=font) > available:
        selected = selected[:-1]
    if selected != text and selected:
        selected = f"{selected[:-1]}…"
    bounds = draw.textbbox((0, 0), selected, font=font)
    draw.text(
        (
            left + (right - left - (bounds[2] - bounds[0])) // 2,
            top + (bottom - top - (bounds[3] - bounds[1])) // 2 - bounds[1],
        ),
        selected,
        font=font,
        fill=fill,
    )


def _draw_image(
    image: Image.Image,
    entity: EntityState,
    area: tuple[int, int, int, int],
) -> bool:
    if not entity.image_bytes or len(entity.image_bytes) > MAX_EMBEDDED_IMAGE_BYTES:
        return False
    left, top, right, bottom = area
    size = (max(1, right - left), max(1, bottom - top))
    try:
        with Image.open(BytesIO(entity.image_bytes)) as source:
            if source.width * source.height > MAX_EMBEDDED_IMAGE_PIXELS:
                return False
            source.load()
            converted = source.convert("RGB")
            if entity.image_fit == "contain":
                fitted = ImageOps.contain(converted, size, Image.Resampling.LANCZOS)
                point = (
                    left + (size[0] - fitted.width) // 2,
                    top + (size[1] - fitted.height) // 2,
                )
            else:
                fitted = ImageOps.fit(converted, size, Image.Resampling.LANCZOS)
                point = (left, top)
            image.paste(fitted, point)
            return True
    except (OSError, UnidentifiedImageError):
        return False


def _draw_tile(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    entity: EntityState,
    box: tuple[int, int, int, int],
    theme: dict[str, RGB],
    accent: RGB,
) -> None:
    left, top, right, bottom = box
    radius = max(8, min(right - left, bottom - top) // 10)
    draw.rounded_rectangle(box, radius=radius, fill=theme["surface"], outline=accent, width=2)
    label_height = max(24, (bottom - top) // 4)
    _draw_text_centered(
        draw,
        (left + 5, top + 3, right - 5, top + label_height),
        entity.label,
        theme["muted"],
        bold=True,
        maximum=14,
    )
    area = (left + 8, top + label_height, right - 8, bottom - 8)
    style = _safe_text(entity.style, 24).lower()
    if style in {"image", "name_card"} and _draw_image(image, entity, area):
        return
    if style == "qr":
        try:
            code = qrcode.QRCode(version=None, box_size=2, border=1)
            code.add_data(_safe_text(entity.state, 256))
            code.make(fit=True)
            rendered = code.make_image(fill_color="black", back_color="white").convert("RGB")
            available = min(area[2] - area[0], area[3] - area[1])
            scale = available // rendered.width
            if scale < 1:
                raise ValueError("QR too dense")
            rendered = rendered.resize(
                (rendered.width * scale, rendered.height * scale),
                Image.Resampling.NEAREST,
            )
            image.paste(
                rendered,
                (
                    area[0] + (area[2] - area[0] - rendered.width) // 2,
                    area[1] + (area[3] - area[1] - rendered.height) // 2,
                ),
            )
            return
        except (DataOverflowError, ValueError):
            pass
    fraction = _fraction(entity)
    if style in {"gauge", "progress"} and fraction is not None:
        bar_height = max(10, (area[3] - area[1]) // 8)
        bar = (area[0], area[3] - bar_height, area[2], area[3])
        draw.rounded_rectangle(bar, radius=bar_height // 2, fill=theme["grid"])
        fill_right = area[0] + round((area[2] - area[0]) * fraction)
        if fill_right > area[0]:
            draw.rounded_rectangle(
                (area[0], bar[1], fill_right, bar[3]),
                radius=bar_height // 2,
                fill=accent,
            )
        area = (area[0], area[1], area[2], bar[1] - 4)
    value = _safe_text(entity.state or "--", 48)
    if entity.unit:
        value = f"{value} {entity.unit}"
    _draw_text_centered(
        draw,
        area,
        value,
        theme["foreground"] if entity.available else theme["danger"],
        bold=True,
        maximum=max(16, min(34, (area[3] - area[1]) // 2)),
    )


def _role_color(theme: dict[str, RGB], role: Any, fallback: RGB) -> RGB:
    return {
        "auto": fallback,
        "primary": theme["accent"],
        "info": theme["accent_secondary"],
        "success": theme["positive"],
        "warning": theme["warning"],
        "danger": theme["danger"],
        "muted": theme["muted"],
        "text": theme["foreground"],
    }.get(_safe_text(role, 16).lower(), fallback)


def _contrast(background: RGB) -> RGB:
    luminance = background[0] * 299 + background[1] * 587 + background[2] * 114
    return (9, 13, 19) if luminance >= 150_000 else (255, 255, 255)


def _draw_manifest_tile(
    draw: ImageDraw.ImageDraw,
    tile: Mapping[str, Any],
    box: tuple[int, int, int, int],
    theme: dict[str, RGB],
    fallback_accent: RGB,
) -> None:
    """Render the same semantic states consumed by the fixed JC receiver."""

    left, top, right, bottom = box
    radius = max(8, min(right - left, bottom - top) // 10)
    role = _role_color(theme, tile.get("color_role"), fallback_accent)
    widget = _safe_text(tile.get("widget"), 24).lower()
    control_style = _safe_text(tile.get("control_style"), 24).lower()
    available = tile.get("available") is True
    checked_toggle = widget == "toggle" and tile.get("on") is True
    actionable = (
        available
        and control_style in {"button", "toggle"}
        and bool(_safe_text(tile.get("action_id"), 64))
    )
    card_fill = role if checked_toggle else theme["surface"]
    outline = role if checked_toggle else theme["grid"]
    if not available:
        card_fill = _mix(theme["surface"], theme["background"], 0.45)
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=card_fill,
        outline=outline,
        width=3 if actionable else 2,
    )

    checked_text = _contrast(role)
    label_color = checked_text if checked_toggle else theme["muted"]
    value_color = (
        checked_text
        if checked_toggle
        else role
        if available
        else theme["danger"]
    )
    hint_color = checked_text if checked_toggle else theme["muted"]
    card_height = bottom - top
    compact = card_height < 130
    label_height = max(20, card_height // 4)
    hint_height = max(17, card_height // 6)
    _draw_text_centered(
        draw,
        (left + 5, top + 3, right - 5, top + label_height),
        _safe_text(tile.get("label"), 48) or "Value",
        label_color,
        bold=True,
        maximum=12 if compact else 16,
    )

    hint = (
        "UNAVAILABLE"
        if not available
        else "TAP TO TOGGLE"
        if actionable and control_style == "toggle"
        else "TAP"
        if actionable
        else "STATUS"
    )
    _draw_text_centered(
        draw,
        (left + 5, bottom - hint_height - 3, right - 5, bottom - 2),
        hint,
        hint_color,
        bold=actionable,
        maximum=9 if compact else 11,
    )

    value_bottom = bottom - hint_height - 5
    progress = tile.get("progress")
    if widget == "gauge" and isinstance(progress, (int, float)) and math.isfinite(progress):
        fraction = max(0.0, min(1.0, float(progress)))
        bar_height = max(8, card_height // 11)
        bar = (left + 10, value_bottom - bar_height, right - 10, value_bottom)
        draw.rounded_rectangle(bar, radius=bar_height // 2, fill=theme["grid"])
        fill_right = bar[0] + round((bar[2] - bar[0]) * fraction)
        if fill_right > bar[0]:
            draw.rounded_rectangle(
                (bar[0], bar[1], fill_right, bar[3]),
                radius=bar_height // 2,
                fill=role,
            )
        value_bottom = bar[1] - 4

    value = _safe_text(tile.get("value"), 32)
    unit = _safe_text(tile.get("unit"), 16)
    if value and unit:
        value = f"{value} {unit}"
    _draw_text_centered(
        draw,
        (left + 8, top + label_height, right - 8, value_bottom),
        value,
        value_color,
        bold=True,
        maximum=max(14, min(30, card_height // 4)),
    )


class ColorDisplayRenderer:
    """Render a deterministic RGB PNG preview for a colour/LVGL display."""

    def __init__(self, theme: Mapping[str, ColorValue] | None = None):
        self._theme = normalise_color_theme(theme)

    def render(
        self,
        profile: DisplayProfile,
        entities: Iterable[EntityState],
        *,
        title: str = "FlexDisplay",
        subtitle: str = "HOME ASSISTANT • LIVE",
        theme: Mapping[str, ColorValue] | None = None,
    ) -> bytes:
        if not isinstance(profile, DisplayProfile) or not profile.is_color or not profile.lvgl:
            raise ColorRenderError("Colour previews require a colour/LVGL display profile")
        values = tuple(islice(iter(entities), MAX_TILES + 1))
        if not 1 <= len(values) <= MAX_TILES or not all(
            isinstance(entity, EntityState) for entity in values
        ):
            raise ColorRenderError("Colour previews require one to four EntityState tiles")

        selected_theme = self._theme if theme is None else normalise_color_theme(theme)
        image = Image.new("RGB", profile.resolution, selected_theme["background"])
        draw = ImageDraw.Draw(image)
        _draw_text_centered(
            draw,
            (35, 10, profile.width - 35, max(42, profile.height // 7)),
            title,
            selected_theme["foreground"],
            bold=True,
            maximum=max(16, profile.width // 17),
        )
        accents = (
            selected_theme["accent"],
            selected_theme["accent_secondary"],
            selected_theme["positive"],
            selected_theme["warning"],
        )
        for index, (entity, box) in enumerate(
            zip(values, _tile_boxes(profile, len(values)), strict=True)
        ):
            _draw_tile(image, draw, entity, box, selected_theme, accents[index])
        _draw_text_centered(
            draw,
            (45, profile.height - max(35, profile.height // 10), profile.width - 45, profile.height - 8),
            subtitle,
            selected_theme["muted"],
            maximum=11,
        )
        if profile.shape == "round":
            mask = Image.new("L", profile.resolution, 0)
            ImageDraw.Draw(mask).ellipse((1, 1, profile.width - 2, profile.height - 2), fill=255)
            clipped = Image.new("RGB", profile.resolution, selected_theme["shadow"])
            clipped.paste(image, (0, 0), mask)
            ImageDraw.Draw(clipped).ellipse(
                (1, 1, profile.width - 2, profile.height - 2),
                outline=selected_theme["grid"],
                width=max(2, min(profile.resolution) // 90),
            )
            image = clipped
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()

    def render_manifest(
        self,
        profile: DisplayProfile,
        page: Mapping[str, Any],
        *,
        subtitle: str = "HOME ASSISTANT • LIVE",
        theme: Mapping[str, ColorValue] | None = None,
    ) -> bytes:
        """Render a page from the canonical bounded LVGL manifest view model."""

        if not isinstance(profile, DisplayProfile) or not profile.is_color or not profile.lvgl:
            raise ColorRenderError("Colour previews require a colour/LVGL display profile")
        raw_tiles = page.get("tiles") if isinstance(page, Mapping) else None
        if not isinstance(raw_tiles, list) or not 1 <= len(raw_tiles) <= MAX_TILES:
            raise ColorRenderError("LVGL previews require one to four manifest tiles")
        if not all(isinstance(tile, Mapping) for tile in raw_tiles):
            raise ColorRenderError("LVGL manifest tiles must be objects")
        layout = _safe_text(page.get("layout"), 16).lower()
        if layout not in {"auto", "single", "rows", "columns", "grid"}:
            raise ColorRenderError("LVGL preview layout is unsupported")

        selected_theme = self._theme if theme is None else normalise_color_theme(theme)
        image = Image.new("RGB", profile.resolution, selected_theme["background"])
        draw = ImageDraw.Draw(image)
        _draw_text_centered(
            draw,
            (35, 10, profile.width - 35, max(42, profile.height // 7)),
            _safe_text(page.get("title"), 48) or "FlexDisplay",
            selected_theme["foreground"],
            bold=True,
            maximum=max(16, profile.width // 17),
        )
        accents = (
            selected_theme["accent"],
            selected_theme["accent_secondary"],
            selected_theme["positive"],
            selected_theme["warning"],
        )
        boxes = _manifest_tile_boxes(profile, len(raw_tiles), layout)
        for index, (tile, box) in enumerate(zip(raw_tiles, boxes, strict=True)):
            _draw_manifest_tile(draw, tile, box, selected_theme, accents[index])
        _draw_text_centered(
            draw,
            (
                45,
                profile.height - max(35, profile.height // 10),
                profile.width - 45,
                profile.height - 8,
            ),
            subtitle,
            selected_theme["muted"],
            maximum=11,
        )
        if profile.shape == "round":
            mask = Image.new("L", profile.resolution, 0)
            ImageDraw.Draw(mask).ellipse(
                (1, 1, profile.width - 2, profile.height - 2), fill=255
            )
            clipped = Image.new("RGB", profile.resolution, selected_theme["shadow"])
            clipped.paste(image, (0, 0), mask)
            ImageDraw.Draw(clipped).ellipse(
                (1, 1, profile.width - 2, profile.height - 2),
                outline=selected_theme["grid"],
                width=max(2, min(profile.resolution) // 90),
            )
            image = clipped
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()


def render_color_preview(
    profile: DisplayProfile,
    entities: Iterable[EntityState],
    *,
    title: str = "FlexDisplay",
    subtitle: str = "HOME ASSISTANT • LIVE",
    theme: Mapping[str, ColorValue] | None = None,
) -> bytes:
    return ColorDisplayRenderer().render(
        profile, entities, title=title, subtitle=subtitle, theme=theme
    )
