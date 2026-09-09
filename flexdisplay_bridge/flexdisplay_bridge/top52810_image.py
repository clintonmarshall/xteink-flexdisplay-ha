"""Bounded offline PNG decoding for the admitted stock tag."""

import base64
import binascii
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from .top52810_renderer import quantize_image

MAX_PNG_BYTES = 131072
MAX_BASE64_CHARS = 4 * ((MAX_PNG_BYTES + 2) // 3)


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
