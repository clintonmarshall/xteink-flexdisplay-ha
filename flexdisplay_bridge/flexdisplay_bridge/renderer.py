from __future__ import annotations

from datetime import datetime
from io import BytesIO
import math
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from .home_assistant import EntityState


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _icon_kind(entity: EntityState) -> str:
    identity = f"{entity.entity_id} {entity.label}".lower()
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


class DashboardRenderer:
    def render(
        self,
        *,
        title: str,
        device: dict[str, Any],
        width: int,
        height: int,
        entities: Iterable[EntityState],
        ha_error: str = "",
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
        title_font = _fit(draw, title, int(width * 0.64), max(25, width // 17), True, 18)
        draw.text((margin, 12), title, fill=255, font=title_font)
        identity = str(device.get("name") or device.get("device_id") or "FlexDisplay")
        identity_font = _fit(draw, identity, int(width * 0.64), max(14, width // 31), False, 12)
        draw.text((margin, header_height - 27), identity, fill=255, font=identity_font)

        now = datetime.now().astimezone()
        time_font = _font(max(22, width // 20), True)
        date_font = _font(max(12, width // 36))
        time_text = now.strftime("%H:%M")
        date_text = now.strftime("%a %d %b")
        time_width = draw.textbbox((0, 0), time_text, font=time_font)[2]
        date_width = draw.textbbox((0, 0), date_text, font=date_font)[2]
        draw.text((width - margin - time_width, 10), time_text, fill=255, font=time_font)
        draw.text((width - margin - date_width, header_height - 27), date_text, fill=255, font=date_font)

        values = list(entities)[:8]
        grid_top = header_height + gap
        footer_top = height - footer_height
        grid_bottom = footer_top - gap

        if values:
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

                icon_size = min(max(36, cell_width // 5), max(36, card_height // 3))
                icon_left = left + 12
                icon_top = top + 12
                _draw_icon(
                    draw,
                    _icon_kind(entity),
                    (icon_left, icon_top, icon_left + icon_size, icon_top + icon_size),
                    entity.state,
                )

                label_left = icon_left + icon_size + 10
                label_width = right - label_left - 10
                label_font = _fit(draw, entity.label, label_width, max(16, width // 29), True, 12)
                draw.text((label_left, top + 15), entity.label, fill=0, font=label_font)

                value = entity.state
                if entity.unit and entity.unit not in value:
                    value = f"{value} {entity.unit}"
                value_font = _fit(draw, value, cell_width - 24, max(31, width // 15), True, 20)
                value_width = draw.textbbox((0, 0), value, font=value_font)[2]
                value_height = draw.textbbox((0, 0), value, font=value_font)[3]
                value_y = bottom - value_height - 17
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

        connection = "HA ERROR" if ha_error else "HA CONNECTED"
        connection_font = _fit(draw, connection, width // 3, max(13, width // 34), True, 11)
        connection_width = draw.textbbox((0, 0), connection, font=connection_font)[2]
        badge_left = width - margin - connection_width - 24
        badge_y = status_y + 5
        draw.ellipse((badge_left, badge_y - 5, badge_left + 10, badge_y + 5), fill=0)
        draw.text((badge_left + 16, status_y - 3), connection, fill=0, font=connection_font)

        output = BytesIO()
        image.convert("1", dither=Image.Dither.NONE).save(output, format="PNG", optimize=True)
        return output.getvalue()
