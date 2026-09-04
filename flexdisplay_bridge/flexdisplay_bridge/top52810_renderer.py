"""Native compact renderer and honest stock-firmware preview for TOP52810.

The renderer is deliberately offline. It creates logical pixels and preview
PNGs only; it has no discovery, Bluetooth, connection, or device-write path.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from .home_assistant import EntityState
from .top52810_codec import (
    BYTES_PER_ROW,
    HEIGHT,
    PIXEL_COUNT,
    PLANE_BYTES,
    WIDTH,
    PixelColor,
    decode_wire_planes,
    encode_pixels,
)


STOCK_OVERLAY_ROWS = (153, 173, 193, 213, 233)
STOCK_OVERLAY_HEIGHT = 10
STOCK_OVERLAY_X = 40
STOCK_OVERLAY_WIDTH = 80
_PALETTE = (
    255,
    255,
    255,  # white
    0,
    0,
    0,  # black
    204,
    0,
    0,  # red
) + (0,) * (256 * 3 - 9)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _palette_image(pixels: Sequence[PixelColor]) -> Image.Image:
    if len(pixels) != PIXEL_COUNT:
        raise ValueError(f"canvas must contain exactly {PIXEL_COUNT} pixels")
    image = Image.new("P", (WIDTH, HEIGHT), PixelColor.WHITE)
    image.putpalette(_PALETTE)
    image.putdata([int(pixel) for pixel in pixels])
    return image


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def pixels_to_png(pixels: Sequence[PixelColor]) -> bytes:
    """Serialize exact black/white/red pixels as a native-size palette PNG."""
    return _png_bytes(_palette_image(pixels))


def quantize_image(image: Image.Image) -> tuple[PixelColor, ...]:
    """Map a native-size image deterministically onto black, white, and red."""
    if image.size != (WIDTH, HEIGHT):
        raise ValueError(f"image must be exactly {WIDTH} x {HEIGHT} pixels")
    pixels: list[PixelColor] = []
    rgb = image.convert("RGB").tobytes()
    for offset in range(0, len(rgb), 3):
        red, green, blue = rgb[offset : offset + 3]
        if red >= 128 and red >= green + 32 and red >= blue + 32:
            pixels.append(PixelColor.RED)
        elif 299 * red + 587 * green + 114 * blue < 145_000:
            pixels.append(PixelColor.BLACK)
        else:
            pixels.append(PixelColor.WHITE)
    return tuple(pixels)


def stock_black_overlay_indices() -> tuple[int, ...]:
    """Return the exact wire-byte indices overwritten by stock firmware."""
    overwritten: list[int] = []
    for index in range(PLANE_BYTES):
        row, column = divmod(index, BYTES_PER_ROW)
        in_overlay_row = any(
            start <= row < start + STOCK_OVERLAY_HEIGHT
            for start in STOCK_OVERLAY_ROWS
        )
        if not in_overlay_row:
            continue
        if column == 1:
            if (0xFF, 0xFD, 0xFE)[index % 3] != 0xFF:
                overwritten.append(index)
        elif 2 <= column <= 10:
            overwritten.append(index)
    return tuple(overwritten)


def apply_stock_black_overlay(black_wire: bytes) -> bytes:
    """Model the fixed black-plane overwrite observed in stock firmware."""
    if not isinstance(black_wire, bytes):
        raise TypeError("black wire plane must be bytes")
    if len(black_wire) != PLANE_BYTES:
        raise ValueError(f"black wire plane must be exactly {PLANE_BYTES} bytes")
    effective = bytearray(black_wire)
    for index in stock_black_overlay_indices():
        _, column = divmod(index, BYTES_PER_ROW)
        effective[index] = (
            (0xFF, 0xFD, 0xFE)[index % 3]
            if column == 1
            else (0xDB, 0x6D, 0xB6)[index % 3]
        )
    return bytes(effective)


def stock_effective_pixels(pixels: Iterable[PixelColor | int]) -> tuple[PixelColor, ...]:
    """Predict visible pixels after the stock black-plane overlay."""
    encoded = encode_pixels(pixels)
    return decode_wire_planes(
        apply_stock_black_overlay(encoded.black_wire),
        encoded.red_wire,
    )


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    maximum_width: int,
    start: int,
) -> tuple[str, ImageFont.ImageFont | ImageFont.FreeTypeFont]:
    normalized = " ".join(str(text or "").split())
    for size in range(start, 7, -1):
        font = _font(size)
        if draw.textbbox((0, 0), normalized, font=font)[2] <= maximum_width:
            return normalized, font
    font = _font(8)
    while normalized and draw.textbbox((0, 0), f"{normalized}…", font=font)[2] > maximum_width:
        normalized = normalized[:-1]
    return f"{normalized}…" if normalized else "", font


def _centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    *,
    fill: PixelColor,
    start_size: int,
) -> None:
    left, top, right, bottom = box
    fitted, font = _fit_text(draw, text, right - left, start_size)
    bounds = draw.textbbox((0, 0), fitted, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text(
        (left + (right - left - width) // 2, top + (bottom - top - height) // 2 - bounds[1]),
        fitted,
        fill=int(fill),
        font=font,
    )


def render_compact_pixels(
    *,
    title: str,
    entities: Iterable[EntityState],
    page_index: int = 0,
    page_count: int = 1,
    ha_error: str = "",
) -> tuple[PixelColor, ...]:
    """Render a native compact canvas while reserving stock-overlay bands."""
    image = _palette_image((PixelColor.WHITE,) * PIXEL_COUNT)
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, WIDTH - 1, 38), fill=int(PixelColor.BLACK))
    _centered_text(
        draw,
        (5, 3, WIDTH - 6, 35),
        title.upper(),
        fill=PixelColor.WHITE,
        start_size=15,
    )
    draw.rectangle((0, 39, WIDTH - 1, 44), fill=int(PixelColor.RED))

    values = list(entities)[:3]
    if not values:
        values = [EntityState("device.bridge", "FlexDisplay", "Ready", "", True)]

    for entity, (top, bottom) in zip(values[:2], ((50, 92), (99, 141)), strict=False):
        draw.rectangle((4, top, WIDTH - 5, bottom), outline=int(PixelColor.BLACK), width=1)
        label, label_font = _fit_text(draw, entity.label, WIDTH - 16, 10)
        draw.text((8, top + 4), label, fill=int(PixelColor.RED), font=label_font)
        value = str(entity.state)
        if entity.unit and entity.unit not in value:
            value = f"{value} {entity.unit}"
        _centered_text(
            draw,
            (8, top + 15, WIDTH - 9, bottom - 3),
            value,
            fill=PixelColor.BLACK,
            start_size=19,
        )

    # A red marker remains controllable, while the adjacent white area shows
    # the five physical hatch bands in the effective stock preview.
    draw.rectangle((5, 150, 31, 245), fill=int(PixelColor.RED))

    if len(values) >= 3:
        entity = values[2]
        draw.rectangle((4, 248, WIDTH - 5, 280), outline=int(PixelColor.BLACK), width=1)
        value = str(entity.state)
        if entity.unit and entity.unit not in value:
            value = f"{value} {entity.unit}"
        _centered_text(
            draw,
            (8, 250, WIDTH - 9, 278),
            f"{entity.label}: {value}",
            fill=PixelColor.BLACK,
            start_size=11,
        )

    draw.rectangle((0, 285, WIDTH - 1, HEIGHT - 1), fill=int(PixelColor.BLACK))
    footer = "HA ERROR" if ha_error else f"{page_index + 1}/{max(1, page_count)}  FLEXDISPLAY"
    _centered_text(
        draw,
        (3, 285, WIDTH - 4, HEIGHT - 1),
        footer,
        fill=PixelColor.WHITE,
        start_size=9,
    )
    return tuple(PixelColor(value) for value in image.tobytes())


def render_compact_preview(
    *,
    title: str,
    entities: Iterable[EntityState],
    page_index: int = 0,
    page_count: int = 1,
    ha_error: str = "",
) -> bytes:
    """Render the expected physical stock-firmware result as a PNG preview."""
    logical = render_compact_pixels(
        title=title,
        entities=entities,
        page_index=page_index,
        page_count=page_count,
        ha_error=ha_error,
    )
    return pixels_to_png(stock_effective_pixels(logical))
