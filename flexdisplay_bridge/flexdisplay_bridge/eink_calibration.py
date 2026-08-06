from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageEnhance, ImageOps


@dataclass(frozen=True)
class EinkCalibration:
    contrast: float
    sharpness: float
    gamma: float
    threshold: int


CALIBRATIONS = {
    "X3": EinkCalibration(contrast=1.12, sharpness=1.05, gamma=0.96, threshold=138),
    "X4": EinkCalibration(contrast=1.34, sharpness=1.18, gamma=0.88, threshold=150),
}


def normalize_model(model: str | None, width: int = 0, height: int = 0) -> str:
    value = str(model or "").upper().replace("XTEINK_", "")
    if value in CALIBRATIONS:
        return value
    return "X3" if (width, height) == (528, 792) else "X4"


def calibrate_monochrome(
    image: Image.Image,
    *,
    model: str | None = None,
    photo: bool = False,
) -> Image.Image:
    """Return a true 1-bit image tuned for the selected XTEINK panel."""
    selected = normalize_model(model, image.width, image.height)
    calibration = CALIBRATIONS[selected]
    grayscale = ImageOps.autocontrast(image.convert("L"))
    grayscale = ImageEnhance.Contrast(grayscale).enhance(calibration.contrast)
    grayscale = ImageEnhance.Sharpness(grayscale).enhance(calibration.sharpness)
    gamma_lut = [round(255 * ((value / 255) ** calibration.gamma)) for value in range(256)]
    grayscale = grayscale.point(gamma_lut)
    if photo:
        return grayscale.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return grayscale.point(
        lambda value: 255 if value >= calibration.threshold else 0,
        mode="1",
    )
