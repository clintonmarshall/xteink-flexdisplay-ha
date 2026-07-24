from __future__ import annotations

from datetime import datetime
from io import BytesIO
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


def _fit(draw: ImageDraw.ImageDraw, text: str, maximum_width: int, start_size: int, bold: bool = False):
    for size in range(start_size, 11, -1):
        selected = _font(size, bold)
        if draw.textbbox((0, 0), text, font=selected)[2] <= maximum_width:
            return selected
    return _font(11, bold)


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
        margin = max(16, width // 20)
        header_height = max(78, height // 10)
        title_font = _fit(draw, title, width - 2 * margin, max(24, width // 14), True)
        small = _font(max(14, width // 28))
        body = _font(max(18, width // 22))
        body_bold = _font(max(18, width // 22), True)

        draw.rectangle((0, 0, width, header_height), fill=0)
        draw.text((margin, 14), title, fill=255, font=title_font)
        identity = str(device.get("name") or device.get("device_id") or "FlexDisplay")
        draw.text((margin, header_height - 27), identity, fill=255, font=small)

        now_text = datetime.now().astimezone().strftime("%a %d %b  %H:%M")
        draw.text((margin, header_height + 22), now_text, fill=0, font=body_bold)
        y = header_height + 66
        draw.line((margin, y, width - margin, y), fill=0, width=2)
        y += 22

        values = list(entities)
        if values:
            row_height = max(58, min(86, (height - y - 120) // max(1, len(values))))
            for entity in values[:7]:
                value = entity.state
                if entity.unit and entity.unit not in value:
                    value = f"{value} {entity.unit}"
                label_font = _fit(draw, entity.label, int(width * 0.55), max(17, width // 22))
                value_font = _fit(draw, value, int(width * 0.38), max(18, width // 20), True)
                draw.text((margin + 4, y + 8), entity.label, fill=0, font=label_font)
                value_width = draw.textbbox((0, 0), value, font=value_font)[2]
                draw.text((width - margin - value_width - 4, y + 8), value, fill=0, font=value_font)
                draw.line((margin + 4, y + row_height - 5, width - margin - 4, y + row_height - 5), fill=175)
                y += row_height
        else:
            box_top = y + 25
            box_bottom = min(height - 135, box_top + 170)
            draw.rounded_rectangle((margin, box_top, width - margin, box_bottom), radius=12, outline=0, width=3)
            draw.text((margin + 24, box_top + 25), "Bridge connected", fill=0, font=body_bold)
            draw.text((margin + 24, box_top + 70), "Add entity IDs to config.yaml", fill=0, font=small)
            draw.text((margin + 24, box_top + 105), "to display live HA values.", fill=0, font=small)

        footer_y = height - 92
        draw.line((margin, footer_y, width - margin, footer_y), fill=0, width=2)
        battery = device.get("battery_percent")
        rssi = device.get("rssi")
        status_bits = []
        if battery is not None:
            status_bits.append(f"Battery {battery}%")
        if rssi is not None:
            status_bits.append(f"Wi-Fi {rssi} dBm")
        status = "  |  ".join(status_bits) or "FlexDisplay check-in received"
        draw.text((margin, footer_y + 15), status, fill=0, font=small)
        if ha_error:
            error_text = "HA API unavailable" if "token not configured" not in ha_error else "HA token not configured"
            draw.text((margin, footer_y + 48), error_text, fill=0, font=small)
        else:
            draw.text((margin, footer_y + 48), "Home Assistant connected", fill=0, font=small)

        output = BytesIO()
        image.convert("1", dither=Image.Dither.FLOYDSTEINBERG).save(output, format="PNG", optimize=True)
        return output.getvalue()
