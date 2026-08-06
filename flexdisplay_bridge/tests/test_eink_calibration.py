from PIL import Image

from flexdisplay_bridge.eink_calibration import calibrate_monochrome, normalize_model


def test_model_is_inferred_from_native_portrait_dimensions() -> None:
    assert normalize_model(None, 528, 792) == "X3"
    assert normalize_model(None, 480, 800) == "X4"


def test_calibration_always_produces_true_one_bit_pixels() -> None:
    gradient = Image.linear_gradient("L").resize((480, 800))
    rendered = calibrate_monochrome(gradient, model="X4", photo=True)
    assert rendered.mode == "1"
    assert rendered.getextrema() == (0, 255)


def test_x4_curve_is_darker_than_x3_for_midtones() -> None:
    gradient = Image.linear_gradient("L").resize((480, 800))
    x3 = calibrate_monochrome(gradient, model="X3", photo=False)
    x4 = calibrate_monochrome(gradient, model="X4", photo=False)
    assert x4.histogram()[0] >= x3.histogram()[0]
