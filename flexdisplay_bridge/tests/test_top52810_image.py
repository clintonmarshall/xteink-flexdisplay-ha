import base64
from io import BytesIO

from PIL import Image
from fastapi.testclient import TestClient
import pytest

from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import BridgeConfig
from flexdisplay_bridge.top52810_image import decode_image, MAX_BASE64_CHARS
from flexdisplay_bridge.top52810_codec import PixelColor


def png(size=(128, 296), color="white", mode="RGB", fmt="PNG"):
    stream = BytesIO()
    Image.new(mode, size, color).save(stream, format=fmt)
    return base64.b64encode(stream.getvalue()).decode()


@pytest.mark.parametrize("value", [None, "", "bad!", "https://example.com/x.png", "A" * (MAX_BASE64_CHARS + 1)])
def test_reject_invalid_input(value):
    with pytest.raises(ValueError):
        decode_image(value)


def test_native_png_palette_and_transparency():
    for color, expected in [("white", PixelColor.WHITE), ("black", PixelColor.BLACK), ("red", PixelColor.RED)]:
        assert set(decode_image(png(color=color))) == {expected}
    assert set(decode_image(png(color=(0, 0, 0, 0), mode="RGBA"))) == {PixelColor.WHITE}
    for value in (png(size=(129, 296)), png(fmt="JPEG"), png(size=(4096, 4096))):
        with pytest.raises(ValueError):
            decode_image(value)


def test_image_preview_and_queue_guards(tmp_path):
    app = create_app(BridgeConfig(state_path=tmp_path / "state.json", api_key="test-key"))
    headers = {"X-FlexDisplay-Bridge-Key": "test-key"}
    path = "/api/v1/stock-ble/top52810"
    payload = {"pattern": "image", "image_base64": png(), "sid": "A1B2C3"}
    address = "DF:84:6B:DE:F6:ED"
    with TestClient(app) as client:
        assert client.post(path + "/plans/preview", json=payload).status_code in {401, 403}
        result = client.post(path + "/plans/preview", json=payload, headers=headers)
        assert result.status_code == 200
        plan = result.json()
        assert plan["device_io"] is False and plan["write_count"] == 44
        assert plan["plan_sha256"] == "8f4e9e35d538f79c43097dc9af58fe2cd8ee340ab259acd1d82bd2755844fa27"
        assert plan["logical_png_base64"] and plan["stock_png_base64"]
        assert app.state.top52810_jobs.pending(address) is None
        payload.update(address=address, expected_name="TRSEPD_F6ED", expected_plan_sha256=plan["plan_sha256"])
        assert client.post(path + "/jobs", json={**payload, "image_base64": png(color="black")}, headers=headers).status_code == 409
        assert client.post(path + "/jobs", json={**payload, "address": "DF:84:6B:DE:F6:EE", "expected_name": "TRSEPD_F6EE"}, headers=headers).status_code == 400
        assert client.post(path + "/jobs", json={**payload, "image_base64": "bad!"}, headers=headers).status_code == 400
        job = client.post(path + "/jobs", json=payload, headers=headers).json()
        assert job["attempt_count"] == 0
        assert "image_base64" not in job
        assert client.post(path + "/jobs", json=payload, headers=headers).status_code == 400
        assert app.state.top52810_jobs.pending(address)["job_id"] == job["job_id"]
