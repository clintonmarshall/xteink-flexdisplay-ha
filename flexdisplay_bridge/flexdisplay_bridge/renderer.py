from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from qrcode.exceptions import DataOverflowError

from .home_assistant import EntityState


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _fit(
    draw: ImageDraw.ImageDraw,
    text: str,
    maximum_width: int,
    start_size: int,
    bold: bool = False,
    minimum_size: int = 13,
):
    for size in range(start_size, minimum_size - 1, -1):
        selected = _font(size, bold)
        if draw.textbbox((0, 0), text, font=selected)[2] <= maximum_width:
            return selected
    return _font(minimum_size, bold)


def _wrap_label(
    draw: ImageDraw.ImageDraw,
    text: str,
    maximum_width: int,
    start_size: int,
    minimum_size: int = 17,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
    words = text.split()
    for size in range(start_size, minimum_size - 1, -1):
        selected = _font(size, True)
        if draw.textbbox((0, 0), text, font=selected)[2] <= maximum_width:
            return selected, [text]
        candidates = [
            (" ".join(words[:split]), " ".join(words[split:]))
            for split in range(1, len(words))
        ]
        fitting = [
            lines
            for lines in candidates
            if max(draw.textbbox((0, 0), line, font=selected)[2] for line in lines)
            <= maximum_width
        ]
        if fitting:
            lines = min(
                fitting,
                key=lambda pair: abs(
                    draw.textbbox((0, 0), pair[0], font=selected)[2]
                    - draw.textbbox((0, 0), pair[1], font=selected)[2]
                ),
            )
            return selected, list(lines)
    return _font(minimum_size, True), [text]


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _icon_kind(entity: EntityState) -> str:
    if entity.icon != "auto":
        return entity.icon
    identity = f"{entity.entity_id} {entity.label}".lower()
    state = entity.state.lower()
    if entity.entity_id.startswith("weather."):
        if any(value in state for value in ("rain", "pour", "lightning")):
            return "rain"
        return "weather"
    if entity.entity_id.startswith(("light.", "switch.")):
        return "light"
    if entity.entity_id.startswith(("lock.", "binary_sensor.")) and any(
        value in identity for value in ("door", "lock", "window")
    ):
        return "lock"
    if any(value in identity for value in ("alarm", "alert", "warning")):
        return "alert"
    if "wifi" in identity or "wi-fi" in identity or "signal" in identity:
        return "wifi"
    if "storage" in identity or "sd card" in identity:
        return "storage"
    if "uptime" in identity or "time" in identity:
        return "clock"
    if "humidity" in identity or "moisture" in identity:
        return "humidity"
    if "temperature" in identity or "_temp" in identity:
        return "temperature"
    if "solar" in identity:
        return "solar"
    if "battery" in identity or "charge" in identity or entity.unit == "%":
        return "battery"
    if "power" in identity or entity.unit in {"W", "kW"}:
        return "power"
    return "home"


def _draw_icon(
    draw: ImageDraw.ImageDraw,
    kind: str,
    box: tuple[int, int, int, int],
    value: str = "",
) -> None:
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    cx = (left + right) // 2
    cy = (top + bottom) // 2
    stroke = max(2, width // 15)

    if kind == "wifi":
        _draw_wifi(draw, left, top, min(width, height))
        return

    if kind == "storage":
        inset = max(5, width // 9)
        draw.rounded_rectangle(
            (left + inset, top + inset // 2, right - inset, bottom - inset // 2),
            radius=stroke,
            outline=0,
            width=stroke,
        )
        draw.polygon(
            [
                (left + width * 2 // 3, top + inset // 2),
                (right - inset, top + inset // 2),
                (right - inset, top + height // 3),
            ],
            fill=0,
        )
        draw.line(
            (left + width // 3, bottom - height // 4, right - width // 3, bottom - height // 4),
            fill=0,
            width=stroke,
        )
        return

    if kind == "clock":
        radius = min(width, height) * 2 // 5
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=0, width=stroke)
        draw.line((cx, cy, cx, cy - radius * 2 // 3), fill=0, width=stroke)
        draw.line((cx, cy, cx + radius // 2, cy + radius // 3), fill=0, width=stroke)
        return

    if kind == "temperature":
        bulb_r = max(5, width // 7)
        tube_top = top + height // 7
        tube_bottom = bottom - height // 4
        draw.rounded_rectangle(
            (cx - stroke * 2, tube_top, cx + stroke * 2, tube_bottom),
            radius=stroke * 2,
            outline=0,
            width=stroke,
        )
        draw.line((cx, tube_top + stroke * 2, cx, tube_bottom), fill=0, width=stroke)
        draw.ellipse(
            (cx - bulb_r, tube_bottom - bulb_r // 2, cx + bulb_r, tube_bottom + bulb_r * 3 // 2),
            outline=0,
            fill=255,
            width=stroke,
        )
        draw.ellipse((cx - bulb_r // 2, tube_bottom, cx + bulb_r // 2, tube_bottom + bulb_r), fill=0)
        return

    if kind == "humidity":
        points = [
            (cx, top + height // 10),
            (left + width // 5, top + height * 3 // 5),
            (left + width // 4, bottom - height // 8),
            (cx, bottom - height // 15),
            (right - width // 4, bottom - height // 8),
            (right - width // 5, top + height * 3 // 5),
        ]
        draw.polygon(points, outline=0)
        draw.arc(
            (left + width // 3, top + height // 2, right - width // 7, bottom - height // 6),
            25,
            115,
            fill=0,
            width=stroke,
        )
        return

    if kind == "battery":
        body = (left + width // 10, top + height // 4, right - width // 7, bottom - height // 5)
        draw.rounded_rectangle(body, radius=stroke, outline=0, width=stroke)
        draw.rectangle((body[2], cy - height // 10, right - width // 14, cy + height // 10), fill=0)
        percent = _number(value)
        if percent is not None:
            percent = max(0.0, min(100.0, percent))
            inner_left = body[0] + stroke * 2
            inner_top = body[1] + stroke * 2
            inner_right = body[2] - stroke * 2
            inner_bottom = body[3] - stroke * 2
            fill_right = inner_left + int((inner_right - inner_left) * percent / 100)
            if fill_right > inner_left:
                draw.rectangle((inner_left, inner_top, fill_right, inner_bottom), fill=0)
        return

    if kind == "solar":
        radius = max(7, width // 6)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=0, width=stroke)
        ray_inner = radius + max(4, width // 12)
        ray_outer = min(width, height) // 2 - 2
        for angle in range(0, 360, 45):
            radians = math.radians(angle)
            draw.line(
                (
                    cx + int(math.cos(radians) * ray_inner),
                    cy + int(math.sin(radians) * ray_inner),
                    cx + int(math.cos(radians) * ray_outer),
                    cy + int(math.sin(radians) * ray_outer),
                ),
                fill=0,
                width=stroke,
            )
        return

    if kind == "power":
        draw.polygon(
            [
                (cx + width // 12, top + height // 12),
                (left + width // 5, cy + height // 12),
                (cx - width // 16, cy + height // 12),
                (cx - width // 8, bottom - height // 12),
                (right - width // 5, cy - height // 12),
                (cx + width // 16, cy - height // 12),
            ],
            fill=0,
        )
        return

    if kind in {"weather", "rain"}:
        sun_r = max(6, width // 8)
        sun_x = left + width // 3
        sun_y = top + height // 3
        draw.ellipse(
            (sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r),
            outline=0,
            width=stroke,
        )
        cloud = (
            left + width // 4,
            top + height * 2 // 5,
            right - width // 8,
            bottom - height // 4,
        )
        draw.rounded_rectangle(cloud, radius=max(5, height // 8), fill=255, outline=0, width=stroke)
        draw.ellipse(
            (
                cloud[0] + width // 9,
                cloud[1] - height // 6,
                cloud[0] + width * 4 // 9,
                cloud[1] + height // 6,
            ),
            fill=255,
            outline=0,
            width=stroke,
        )
        if kind == "rain":
            for offset in (width // 3, width // 2, width * 2 // 3):
                draw.line(
                    (left + offset, bottom - height // 5, left + offset - stroke, bottom),
                    fill=0,
                    width=stroke,
                )
        return

    if kind == "light":
        radius = min(width, height) // 4
        draw.ellipse((cx - radius, top + height // 10, cx + radius, cy + radius // 2), outline=0, width=stroke)
        draw.line((cx - radius // 2, cy + radius // 3, cx + radius // 2, cy + radius // 3), fill=0, width=stroke)
        draw.line((cx - radius // 3, cy + radius // 2, cx + radius // 3, cy + radius // 2), fill=0, width=stroke)
        return

    if kind == "lock":
        body_top = cy - height // 12
        draw.rounded_rectangle(
            (left + width // 5, body_top, right - width // 5, bottom - height // 10),
            radius=stroke,
            outline=0,
            width=stroke,
        )
        draw.arc(
            (left + width // 3, top + height // 10, right - width // 3, cy + height // 8),
            180,
            360,
            fill=0,
            width=stroke,
        )
        draw.ellipse((cx - stroke, cy + stroke, cx + stroke, cy + stroke * 3), fill=0)
        return

    if kind == "alert":
        draw.polygon(
            [
                (cx, top + height // 12),
                (right - width // 10, bottom - height // 10),
                (left + width // 10, bottom - height // 10),
            ],
            outline=0,
        )
        draw.line((cx, top + height // 3, cx, bottom - height // 3), fill=0, width=stroke)
        draw.ellipse((cx - stroke, bottom - height // 4, cx + stroke, bottom - height // 4 + stroke * 2), fill=0)
        return

    roof_y = top + height // 3
    draw.line((left + width // 8, roof_y, cx, top + height // 10), fill=0, width=stroke)
    draw.line((cx, top + height // 10, right - width // 8, roof_y), fill=0, width=stroke)
    draw.rectangle((left + width // 4, roof_y, right - width // 4, bottom - height // 8), outline=0, width=stroke)
    draw.rectangle((cx - width // 12, bottom - height // 3, cx + width // 12, bottom - height // 8), fill=0)


def _draw_wifi(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    stroke = max(2, size // 9)
    draw.arc((x, y, x + size, y + size), 210, 330, fill=0, width=stroke)
    inset = size // 4
    draw.arc((x + inset, y + inset, x + size - inset, y + size - inset), 210, 330, fill=0, width=stroke)
    draw.ellipse(
        (x + size // 2 - stroke, y + size * 3 // 4, x + size // 2 + stroke, y + size * 3 // 4 + stroke * 2),
        fill=0,
    )


def _draw_status_battery(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    percent: Any,
) -> None:
    draw.rectangle((x, y, x + width - 4, y + height), outline=0, width=2)
    draw.rectangle((x + width - 3, y + height // 3, x + width, y + height * 2 // 3), fill=0)
    numeric = _number(str(percent))
    if numeric is not None:
        inner_width = max(0, int((width - 8) * max(0.0, min(100.0, numeric)) / 100))
        draw.rectangle((x + 3, y + 3, x + 3 + inner_width, y + height - 3), fill=0)


def _normalized(entity: EntityState) -> float | None:
    value = _number(entity.state)
    if value is None or entity.maximum <= entity.minimum:
        return None
    return max(0.0, min(1.0, (value - entity.minimum) / (entity.maximum - entity.minimum)))


def _draw_tile_visual(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    entity: EntityState,
    box: tuple[int, int, int, int],
) -> None:
    """Draw the selected e-ink-safe visual treatment in the tile's hero area."""
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    if entity.style == "qr":
        try:
            code = qrcode.QRCode(version=None, box_size=1, border=2)
            code.add_data(entity.state)
            code.make(fit=True)
            qr = code.make_image(fill_color="black", back_color="white").convert("L")
            size = min(width, height)
            scale = max(1, size // qr.width)
            rendered_size = qr.width * scale
            if rendered_size > size:
                raise ValueError("QR content is too dense for this tile")
            image.paste(
                qr.resize((rendered_size, rendered_size), Image.Resampling.NEAREST),
                (
                    left + (width - rendered_size) // 2,
                    top + (height - rendered_size) // 2,
                ),
            )
        except (DataOverflowError, ValueError):
            _draw_icon(draw, "alert", box)
        return

    fraction = _normalized(entity)
    stroke = max(3, width // 28)
    if entity.style == "progress":
        bar_height = max(20, height // 3)
        bar_top = top + (height - bar_height) // 2
        draw.rounded_rectangle(
            (left, bar_top, right, bar_top + bar_height),
            radius=bar_height // 3,
            outline=0,
            width=stroke,
        )
        if fraction is not None:
            inner = stroke + 2
            fill_right = left + inner + int((width - inner * 2) * fraction)
            if fill_right > left + inner:
                draw.rounded_rectangle(
                    (left + inner, bar_top + inner, fill_right, bar_top + bar_height - inner),
                    radius=max(2, bar_height // 5),
                    fill=0,
                )
        return

    if entity.style == "gauge":
        inset = max(3, stroke)
        draw.arc(
            (left + inset, top + inset, right - inset, bottom * 2 - top - inset),
            180,
            360,
            fill=0,
            width=stroke,
        )
        if fraction is not None:
            angle = math.radians(180 + 180 * fraction)
            cx = (left + right) // 2
            cy = bottom
            radius = width // 2 - inset * 2
            draw.line(
                (cx, cy, cx + int(math.cos(angle) * radius), cy + int(math.sin(angle) * radius)),
                fill=0,
                width=stroke,
            )
            draw.ellipse((cx - stroke, cy - stroke, cx + stroke, cy + stroke), fill=0)
        return

    if entity.style == "history":
        values = list(entity.history)
        if len(values) >= 2:
            low = min(values)
            high = max(values)
            span = high - low or 1
            points = [
                (
                    left + round(index * width / (len(values) - 1)),
                    bottom - round((value - low) * height / span),
                )
                for index, value in enumerate(values)
            ]
            draw.line(points, fill=0, width=stroke, joint="curve")
            draw.line((left, bottom, right, bottom), fill=0, width=1)
            return

    _draw_icon(draw, _icon_kind(entity), box, entity.state)


def _draw_image_tile(
    canvas: Image.Image,
    entity: EntityState,
    box: tuple[int, int, int, int],
) -> bool:
    """Decode, crop or contain, and dither a downloaded image for e-paper."""
    if not entity.image_bytes:
        return False
    left, top, right, bottom = box
    target = (max(1, right - left), max(1, bottom - top))
    try:
        with Image.open(BytesIO(entity.image_bytes)) as source:
            normalized = ImageOps.autocontrast(ImageOps.exif_transpose(source).convert("L"))
            if entity.image_fit == "contain":
                rendered = Image.new("L", target, 255)
                contained = ImageOps.contain(normalized, target, Image.Resampling.LANCZOS)
                rendered.paste(
                    contained,
                    ((target[0] - contained.width) // 2, (target[1] - contained.height) // 2),
                )
            else:
                rendered = ImageOps.fit(
                    normalized,
                    target,
                    Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            dithered = rendered.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
            canvas.paste(dithered, (left, top))
            return True
    except (UnidentifiedImageError, OSError, ValueError):
        return False


def _draw_badge_background(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    theme: str,
) -> tuple[int, int]:
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    header_height = max(38, min(62, height // 6))
    header_bottom = top + header_height
    if theme == "bold":
        draw.rectangle((left, top, right, header_bottom), fill=0)
        draw.rectangle((left, header_bottom, left + max(8, width // 35), bottom), fill=0)
    elif theme == "diagonal":
        draw.polygon(
            [
                (left, top),
                (right, top),
                (right - width // 8, header_bottom),
                (left, header_bottom),
            ],
            fill=0,
        )
        spacing = max(14, width // 28)
        pattern_height = max(36, min(height // 3, width // 4))
        for x in range(left, right, spacing):
            draw.line(
                (
                    x,
                    bottom,
                    min(right, x + pattern_height),
                    bottom - min(pattern_height, right - x),
                ),
                fill=0,
                width=1,
            )
    elif theme == "halftone":
        dot = max(2, width // 150)
        spacing = max(10, width // 38)
        corner_width = width // 4
        corner_height = height // 3
        for y in range(top + spacing, top + corner_height, spacing):
            for x in range(left + spacing, left + corner_width, spacing):
                if (x + y) // spacing % 2 == 0:
                    draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=0)
        for y in range(bottom - corner_height, bottom - spacing, spacing):
            for x in range(right - corner_width, right - spacing, spacing):
                if (x + y) // spacing % 2 == 0:
                    draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=0)
        draw.rectangle((left, top, right, header_bottom), outline=0, width=3)
    else:
        draw.line((left, header_bottom, right, header_bottom), fill=0, width=2)
    return header_bottom, 255 if theme in {"bold", "diagonal"} else 0


def _draw_badge_portrait(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    entity: EntityState,
    box: tuple[int, int, int, int],
    *,
    rounded: bool,
) -> None:
    left, top, right, bottom = box
    width = max(1, right - left)
    height = max(1, bottom - top)
    stroke = max(3, min(width, height) // 32)
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    if rounded:
        mask_draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=max(12, width // 7),
            fill=255,
        )
    else:
        mask_draw.ellipse((0, 0, width - 1, height - 1), fill=255)

    rendered = False
    if entity.image_bytes:
        try:
            with Image.open(BytesIO(entity.image_bytes)) as source:
                normalized = ImageOps.autocontrast(
                    ImageOps.exif_transpose(source).convert("L")
                )
                fitted = ImageOps.fit(
                    normalized,
                    (width, height),
                    Image.Resampling.LANCZOS,
                    centering=(0.5, 0.38),
                )
                dithered = fitted.convert(
                    "1",
                    dither=Image.Dither.FLOYDSTEINBERG,
                ).convert("L")
                canvas.paste(dithered, (left, top), mask)
                rendered = True
        except (UnidentifiedImageError, OSError, ValueError):
            rendered = False

    if not rendered:
        center_x = (left + right) // 2
        head_radius = max(8, width // 7)
        head_top = top + height // 5
        draw.ellipse(
            (
                center_x - head_radius,
                head_top,
                center_x + head_radius,
                head_top + head_radius * 2,
            ),
            fill=0,
        )
        shoulder_top = top + height // 2
        draw.pieslice(
            (
                center_x - width // 3,
                shoulder_top,
                center_x + width // 3,
                shoulder_top + height // 2,
            ),
            180,
            360,
            fill=0,
        )
    if rounded:
        draw.rounded_rectangle(
            box,
            radius=max(12, width // 7),
            outline=0,
            width=stroke,
        )
    else:
        draw.ellipse(box, outline=0, width=stroke)


def _draw_name_card(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    entity: EntityState,
    box: tuple[int, int, int, int],
) -> None:
    """Render a themed, photo-capable, high-contrast identification card."""
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    center_x = (left + right) // 2
    padding = max(14, width // 24)
    theme = entity.badge_theme if entity.badge_theme in {
        "classic",
        "bold",
        "diagonal",
        "halftone",
    } else "classic"
    header_bottom, header_fill = _draw_badge_background(draw, box, theme)
    badge = "IDENTIFICATION"
    badge_font = _font(max(13, min(22, width // 25)), True)
    badge_width = draw.textbbox((0, 0), badge, font=badge_font)[2]
    draw.text(
        (center_x - badge_width // 2, top + max(9, (header_bottom - top) // 4)),
        badge,
        fill=header_fill,
        font=badge_font,
    )

    if width >= height * 1.2:
        content_top = header_bottom + max(12, height // 28)
        portrait_size = min(
            height - (content_top - top) - padding,
            max(92, width // 3),
        )
        portrait_left = left + padding + (max(0, width // 3 - portrait_size) // 2)
        portrait_top = content_top + max(
            0,
            (bottom - padding - content_top - portrait_size) // 2,
        )
        _draw_badge_portrait(
            canvas,
            draw,
            entity,
            (
                portrait_left,
                portrait_top,
                portrait_left + portrait_size,
                portrait_top + portrait_size,
            ),
            rounded=theme in {"bold", "diagonal"},
        )
        text_left = left + max(width // 3, portrait_left + portrait_size - left) + padding
        text_width = max(80, right - padding - text_left)
        name_font, name_lines = _wrap_label(
            draw,
            entity.label,
            text_width,
            max(28, width // 13),
            max(18, width // 23),
        )
        name_y = content_top + max(2, height // 55)
        for line in name_lines[:2]:
            bounds = draw.textbbox((0, 0), line, font=name_font)
            draw.text((text_left, name_y), line, fill=0, font=name_font)
            name_y += bounds[3] - bounds[1] + 4
        role = entity.state or "Team member"
        role_font = _fit(draw, role, text_width, max(19, width // 24), True, 13)
        role_y = min(bottom - 70, name_y + max(8, height // 35))
        draw.text((text_left, role_y), role, fill=0, font=role_font)
        accent_y = role_y + draw.textbbox((0, 0), role, font=role_font)[3] + 7
        draw.line(
            (text_left, accent_y, min(right - padding, text_left + text_width * 2 // 3), accent_y),
            fill=0,
            width=max(2, width // 180),
        )
        if entity.unit:
            detail_font = _fit(
                draw,
                entity.unit,
                text_width,
                max(16, width // 29),
                False,
                11,
            )
            draw.text(
                (text_left, min(bottom - padding - 18, accent_y + 10)),
                entity.unit,
                fill=0,
                font=detail_font,
            )
        return

    portrait_size = min(max(82, width // 3), max(82, height // 3))
    portrait_top = header_bottom + max(14, height // 40)
    portrait_left = center_x - portrait_size // 2
    _draw_badge_portrait(
        canvas,
        draw,
        entity,
        (
            portrait_left,
            portrait_top,
            portrait_left + portrait_size,
            portrait_top + portrait_size,
        ),
        rounded=theme in {"bold", "diagonal"},
    )
    name_top = portrait_top + portrait_size + max(14, height // 45)
    name_font, name_lines = _wrap_label(
        draw,
        entity.label,
        width - padding * 2,
        max(34, width // 10),
        max(21, width // 18),
    )
    name_y = name_top
    for line in name_lines[:2]:
        bounds = draw.textbbox((0, 0), line, font=name_font)
        line_width = bounds[2] - bounds[0]
        draw.text((center_x - line_width // 2, name_y), line, fill=0, font=name_font)
        name_y += bounds[3] - bounds[1] + 5
    role = entity.state or "Team member"
    role_font = _fit(
        draw,
        role,
        width - padding * 2,
        max(22, width // 17),
        True,
        14,
    )
    role_width = draw.textbbox((0, 0), role, font=role_font)[2]
    role_y = min(bottom - max(84, height // 7), name_y + max(10, height // 50))
    draw.text((center_x - role_width // 2, role_y), role, fill=0, font=role_font)
    if entity.unit:
        detail_font = _fit(
            draw,
            entity.unit,
            width - padding * 2,
            max(18, width // 23),
            False,
            12,
        )
        detail_width = draw.textbbox((0, 0), entity.unit, font=detail_font)[2]
        detail_y = min(bottom - padding - 20, role_y + max(34, height // 18))
        draw.text(
            (center_x - detail_width // 2, detail_y),
            entity.unit,
            fill=0,
            font=detail_font,
        )


class DashboardRenderer:
    def render(
        self,
        *,
        title: str,
        device: dict[str, Any],
        width: int,
        height: int,
        entities: Iterable[EntityState],
        page_index: int = 0,
        page_count: int = 1,
        ha_error: str = "",
        layout: str = "auto",
    ) -> bytes:
        width = max(240, min(1200, width))
        height = max(240, min(1600, height))
        image = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(image)
        margin = max(14, width // 26)
        gap = max(8, width // 40)
        header_height = max(82, height // 10)
        footer_height = max(42, height // 18)

        draw.rectangle((0, 0, width, header_height), fill=0)
        title_font = _fit(draw, title, int(width * 0.68), max(30, width // 14), True, 22)
        draw.text((margin, 12), title, fill=255, font=title_font)
        identity = str(device.get("name") or device.get("device_id") or "FlexDisplay")
        page_label = f"{identity}  •  {page_index + 1}/{max(1, page_count)}"
        identity_font = _fit(draw, page_label, int(width * 0.68), max(15, width // 29), False, 12)
        draw.text((margin, header_height - 28), page_label, fill=255, font=identity_font)

        now = datetime.now().astimezone()
        time_font = _font(max(22, width // 20), True)
        date_font = _font(max(12, width // 36))
        time_text = now.strftime("%H:%M")
        date_text = now.strftime("%a %d %b")
        time_width = draw.textbbox((0, 0), time_text, font=time_font)[2]
        date_width = draw.textbbox((0, 0), date_text, font=date_font)[2]
        draw.text((width - margin - time_width, 10), time_text, fill=255, font=time_font)
        draw.text((width - margin - date_width, header_height - 27), date_text, fill=255, font=date_font)

        values = list(entities)[:4]
        grid_top = header_height + gap
        footer_top = height - footer_height
        grid_bottom = footer_top - gap

        if values:
            if layout == "single" or len(values) == 1:
                columns, rows = 1, 1
            elif layout == "rows" or (layout == "auto" and len(values) == 2):
                columns, rows = 1, len(values)
            elif layout == "columns":
                columns, rows = len(values), 1
            else:
                columns = 2 if width >= 380 else 1
                rows = math.ceil(len(values) / columns)
            card_width = (width - margin * 2 - gap * (columns - 1)) // columns
            card_height = (grid_bottom - grid_top - gap * (rows - 1)) // rows
            for index, entity in enumerate(values):
                column = index % columns
                row = index // columns
                left = margin + column * (card_width + gap)
                top = grid_top + row * (card_height + gap)
                cell_width = card_width
                if columns == 2 and len(values) % 2 == 1 and index == len(values) - 1:
                    cell_width = card_width * 2 + gap
                right = left + cell_width
                bottom = top + card_height
                draw.rounded_rectangle(
                    (left, top, right, bottom),
                    radius=max(8, width // 45),
                    outline=0,
                    width=2,
                )

                if entity.style == "name_card":
                    _draw_name_card(
                        image,
                        draw,
                        entity,
                        (left + 4, top + 4, right - 4, bottom - 4),
                    )
                    continue

                if entity.style == "image":
                    inset = 4
                    caption_height = max(46, min(74, card_height // 7))
                    image_bottom = bottom - caption_height
                    rendered = _draw_image_tile(
                        image,
                        entity,
                        (left + inset, top + inset, right - inset, image_bottom),
                    )
                    if not rendered:
                        placeholder_size = min(92, cell_width // 3, max(48, card_height // 4))
                        placeholder_left = left + (cell_width - placeholder_size) // 2
                        placeholder_top = top + max(16, (image_bottom - top - placeholder_size) // 2)
                        _draw_icon(
                            draw,
                            "alert",
                            (
                                placeholder_left,
                                placeholder_top,
                                placeholder_left + placeholder_size,
                                placeholder_top + placeholder_size,
                            ),
                            entity.state,
                        )
                    draw.rectangle((left + 2, image_bottom, right - 2, bottom - 2), fill=255)
                    draw.line((left + 2, image_bottom, right - 2, image_bottom), fill=0, width=2)
                    caption_font = _fit(
                        draw,
                        entity.label,
                        cell_width - 24,
                        max(20, width // 21),
                        True,
                        13,
                    )
                    caption_box = draw.textbbox((0, 0), entity.label, font=caption_font)
                    caption_width = caption_box[2] - caption_box[0]
                    caption_height_text = caption_box[3] - caption_box[1]
                    draw.text(
                        (
                            left + (cell_width - caption_width) // 2,
                            image_bottom + (caption_height - caption_height_text) // 2 - caption_box[1],
                        ),
                        entity.label,
                        fill=0,
                        font=caption_font,
                    )
                    continue

                icon_size = min(
                    max(58, cell_width // (2 if len(values) == 1 else 3)),
                    max(58, card_height // (2 if len(values) == 1 else 3)),
                )
                icon_left = left + (cell_width - icon_size) // 2
                icon_top = top + 18
                _draw_tile_visual(
                    image,
                    draw,
                    entity,
                    (icon_left, icon_top, icon_left + icon_size, icon_top + icon_size),
                )

                label_width = cell_width - 24
                label_font, label_lines = _wrap_label(
                    draw,
                    entity.label,
                    label_width,
                    max(23, width // 20),
                )
                label_y = icon_top + icon_size + 12
                for line in label_lines:
                    label_bbox = draw.textbbox((0, 0), line, font=label_font)
                    label_text_width = label_bbox[2] - label_bbox[0]
                    draw.text(
                        (left + (cell_width - label_text_width) // 2, label_y),
                        line,
                        fill=0,
                        font=label_font,
                    )
                    label_y += label_bbox[3] - label_bbox[1] + 4

                value = entity.unit or "SCAN ME" if entity.style == "qr" else entity.state
                if entity.unit and entity.unit not in value:
                    value = f"{value} {entity.unit}"
                value_font = _fit(draw, value, cell_width - 20, max(52, width // 9), True, 22)
                value_width = draw.textbbox((0, 0), value, font=value_font)[2]
                value_height = draw.textbbox((0, 0), value, font=value_font)[3]
                value_y = bottom - value_height - 26
                draw.text((left + (cell_width - value_width) // 2, value_y), value, fill=0, font=value_font)
        else:
            box_top = grid_top + 28
            box_bottom = min(grid_bottom - 20, box_top + 230)
            draw.rounded_rectangle((margin, box_top, width - margin, box_bottom), radius=14, outline=0, width=3)
            _draw_icon(draw, "home", (margin + 24, box_top + 28, margin + 88, box_top + 92))
            heading = _font(max(24, width // 18), True)
            body = _font(max(16, width // 28))
            draw.text((margin + 106, box_top + 34), "Bridge connected", fill=0, font=heading)
            draw.text((margin + 24, box_top + 125), "Add entity IDs to config.yaml", fill=0, font=body)
            draw.text((margin + 24, box_top + 160), "to display live Home Assistant values.", fill=0, font=body)

        draw.line((margin, footer_top, width - margin, footer_top), fill=0, width=2)
        status_font = _font(max(13, width // 34), True)
        status_y = footer_top + max(10, footer_height // 4)
        cursor_x = margin
        battery = device.get("battery_percent")
        if battery is not None:
            _draw_status_battery(draw, cursor_x, status_y, 25, 13, battery)
            cursor_x += 32
            battery_text = f"{battery}%"
            draw.text((cursor_x, status_y - 3), battery_text, fill=0, font=status_font)
            cursor_x += draw.textbbox((0, 0), battery_text, font=status_font)[2] + 18
        rssi = device.get("rssi")
        if rssi is not None:
            _draw_wifi(draw, cursor_x, status_y - 6, 22)
            cursor_x += 28
            draw.text((cursor_x, status_y - 3), f"{rssi} dBm", fill=0, font=status_font)

        uses_home_assistant = any(
            not entity.entity_id.startswith(("static.", "device.", "image_url."))
            for entity in values
        )
        connection = (
            "HA ERROR"
            if ha_error
            else "HA CONNECTED"
            if uses_home_assistant
            else "STANDALONE"
        )
        connection_font = _fit(draw, connection, width // 3, max(13, width // 34), True, 11)
        connection_width = draw.textbbox((0, 0), connection, font=connection_font)[2]
        badge_left = width - margin - connection_width - 24
        badge_y = status_y + 5
        draw.ellipse((badge_left, badge_y - 5, badge_left + 10, badge_y + 5), fill=0)
        draw.text((badge_left + 16, status_y - 3), connection, fill=0, font=connection_font)

        output = BytesIO()
        image.convert("1", dither=Image.Dither.NONE).save(output, format="PNG", optimize=True)
        return output.getvalue()
