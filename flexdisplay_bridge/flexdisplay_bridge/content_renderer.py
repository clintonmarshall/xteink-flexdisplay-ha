from __future__ import annotations

import io
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

from .content_channels import ContentPage


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def render_content_page(
    page: ContentPage,
    *,
    device_name: str,
    width: int,
    height: int,
    page_index: int,
    page_count: int,
) -> bytes:
    width = max(240, min(1200, width))
    height = max(240, min(1600, height))
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)
    margin = max(18, width // 22)
    header = max(88, height // 9)
    footer = max(56, height // 14)
    priority = page.priority if page.priority in {"normal", "important", "critical"} else "normal"

    draw.rectangle((0, 0, width, header), fill=0)
    if priority == "critical":
        draw.rectangle((0, header, width, header + max(12, height // 60)), fill=0)
    title_font = _font(max(27, width // 15), True)
    title = page.title.upper()[:80]
    while title_font.size > 18 and draw.textbbox((0, 0), title, font=title_font)[2] > width - margin * 2:
        title_font = _font(title_font.size - 1, True)
    draw.text((margin, max(10, (header - title_font.size) // 3)), title, fill=255, font=title_font)
    meta_font = _font(max(13, width // 35))
    meta = f"{device_name}  ·  {page_index + 1}/{max(1, page_count)}  ·  {page.kind.title()}"
    draw.text((margin, header - max(27, meta_font.size + 8)), meta, fill=255, font=meta_font)

    content_top = header + max(24, height // 28)
    content_bottom = height - footer - max(14, height // 45)
    qr_size = 0
    qr_image = None
    if page.link:
        qr_size = min(max(92, width // 4), max(92, (content_bottom - content_top) // 3))
        qr = qrcode.QRCode(version=None, box_size=4, border=2)
        qr.add_data(page.link)
        qr.make(fit=True)
        qr_image = qr.make_image(fill_color="black", back_color="white").convert("L")
        qr_image = qr_image.resize((qr_size, qr_size), Image.Resampling.NEAREST)

    text_width = width - margin * 2
    if qr_image is not None and width >= 520:
        text_width -= qr_size + margin
    start_size = max(31, width // (10 if page.kind == "quote" else 13))
    minimum_size = max(17, width // 28)
    selected_font = _font(start_size, page.kind in {"message", "news"})
    lines: list[str] = []
    while selected_font.size >= minimum_size:
        lines = _wrap(draw, page.body, selected_font, text_width)
        line_height = int(selected_font.size * 1.34)
        if len(lines) * line_height <= content_bottom - content_top:
            break
        selected_font = _font(selected_font.size - 1, page.kind in {"message", "news"})
    line_height = int(selected_font.size * 1.34)
    y = content_top
    if page.kind == "quote":
        quote_font = _font(max(42, width // 8), True)
        draw.text((margin, y - 10), "“", fill=0, font=quote_font)
        y += quote_font.size // 2
    for line in lines:
        if y + line_height > content_bottom:
            break
        draw.text((margin, y), line, fill=0, font=selected_font)
        y += line_height

    if qr_image is not None:
        if width >= 520:
            canvas.paste(qr_image, (width - margin - qr_size, content_bottom - qr_size))
        else:
            size = min(qr_size, max(70, content_bottom - y - 8))
            if size >= 70:
                canvas.paste(qr_image.resize((size, size), Image.Resampling.NEAREST), ((width - size) // 2, content_bottom - size))

    footer_top = height - footer
    draw.line((margin, footer_top, width - margin, footer_top), fill=0, width=max(1, width // 300))
    footer_font = _font(max(14, width // 31), True)
    footer_text = (page.footer or page.source or "FlexDisplay")[:160]
    while footer_font.size > 11 and draw.textbbox((0, 0), footer_text, font=footer_font)[2] > width - margin * 2:
        footer_font = _font(footer_font.size - 1, True)
    draw.text((margin, footer_top + max(12, footer // 4)), footer_text, fill=0, font=footer_font)

    output = io.BytesIO()
    canvas.convert("1", dither=Image.Dither.FLOYDSTEINBERG).save(output, format="PNG", optimize=True)
    return output.getvalue()
