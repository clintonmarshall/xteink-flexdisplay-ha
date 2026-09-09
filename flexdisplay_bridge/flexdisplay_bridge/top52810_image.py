"""Bounded offline PNG decoding for the admitted stock tag."""

import base64
import binascii
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from .top52810_renderer import quantize_image

MAX_PNG_BYTES = 131072
MAX_BASE64_CHARS = 4 * ((MAX_PNG_BYTES + 2) // 3)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_PIXELS = 12_000_000


def prepare_image(raw: bytes, resize_mode: str = "fit") -> str:
    """Convert a bounded PNG/JPEG to the canonical native PNG, offline."""
    if resize_mode not in {"fit", "crop"}:
        raise ValueError("resize_mode must be fit or crop")
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Image must be at most 5 MiB")
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG"}:
                raise ValueError("Choose a PNG or JPEG image")
            if source.width * source.height > MAX_UPLOAD_PIXELS:
                raise ValueError("Image must contain at most 12 million pixels")
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("Animated images are not supported")
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            rgba = ImageOps.exif_transpose(source).convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            white.alpha_composite(rgba)
            rgb = white.convert("RGB")
            if resize_mode == "crop":
                canvas = ImageOps.fit(rgb, (128, 296), method=Image.Resampling.LANCZOS)
            else:
                fitted = ImageOps.contain(rgb, (128, 296), method=Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", (128, 296), "white")
                canvas.paste(fitted, ((128 - fitted.width) // 2, (296 - fitted.height) // 2))
            from .top52810_renderer import pixels_to_png
            return base64.b64encode(pixels_to_png(quantize_image(canvas))).decode("ascii")
    except (OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as err:
        raise ValueError("Invalid or oversized PNG/JPEG image") from err


def decode_image(value):
    """Accept one exact-size PNG; never fetch URLs or open caller paths."""
    if not isinstance(value, str) or not value or len(value) > MAX_BASE64_CHARS:
        raise ValueError("image_base64 must contain a PNG of at most 128 KiB")
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) > MAX_PNG_BYTES:
            raise ValueError("PNG exceeds 128 KiB")
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG" or image.size != (128, 296):
                raise ValueError("PNG must be exactly 128 x 296 pixels")
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Animated PNG is not supported")
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            rgba = image.convert("RGBA")
            white = Image.new("RGBA", image.size, "white")
            white.alpha_composite(rgba)
            return quantize_image(white.convert("RGB"))
    except (binascii.Error, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, SyntaxError) as err:
        raise ValueError("Invalid PNG or base64 image") from err
