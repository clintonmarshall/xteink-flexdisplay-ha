from __future__ import annotations

import hashlib
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import (
    BridgeConfig,
    DashboardPageConfig,
    DashboardProfileConfig,
    EntityConfig,
)
from flexdisplay_bridge.display_profiles import (
    DisplayProfileStore,
    TOP52810_PROFILE,
    profile_payload,
)
from flexdisplay_bridge.home_assistant import EntityState
from flexdisplay_bridge.top52810_codec import PIXEL_COUNT, PixelColor
from flexdisplay_bridge.top52810_renderer import (
    STOCK_OVERLAY_HEIGHT,
    STOCK_OVERLAY_ROWS,
    STOCK_OVERLAY_WIDTH,
    STOCK_OVERLAY_X,
    quantize_image,
    render_compact_pixels,
    render_compact_preview,
    stock_black_overlay_indices,
    stock_effective_pixels,
)


def _config(tmp_path) -> BridgeConfig:
    return BridgeConfig(
        state_path=tmp_path / "state.json",
        profiles={
            "default": DashboardProfileConfig(
                name="default",
                pages=(
                    DashboardPageConfig(
                        title="Overview",
                        entities=(
                            EntityConfig(
                                "static.power",
                                "Power",
                                source="static",
                                value="42",
                                unit="W",
                            ),
                        ),
                    ),
                ),
            )
        },
        default_profile="default",
    )


def test_builtin_profile_exposes_stock_constraints_without_upload_capabilities(
    tmp_path,
) -> None:
    payload = profile_payload(TOP52810_PROFILE)
    assert payload["id"] == "top52810m_d01_stock"
    assert payload["model"] == "TOP52810M-D01"
    assert (payload["width"], payload["height"]) == (128, 296)
    assert payload["pixel_format"] == "BWR1"
    assert payload["palette"] == ["black", "white", "red"]
    assert payload["transport"] == "stock_ble"
    assert payload["arbitrary_full_canvas"] is False
    assert payload["touch"] is False
    assert payload["lvgl"] is False
    assert payload["unsafe_regions"] == [
        {
            "x": 40,
            "y": y,
            "width": 80,
            "height": 10,
            "affected_colors": ["black", "white"],
            "reason": "stock_firmware_black_plane_overlay",
        }
        for y in STOCK_OVERLAY_ROWS
    ]

    store = DisplayProfileStore(tmp_path / "profiles.json")
    assert store.resolve("TOP52810M-D01") == TOP52810_PROFILE
    assert store.resolve("MS136F6 V1.0") == TOP52810_PROFILE


def test_overlay_model_matches_physically_verified_byte_scope() -> None:
    indices = stock_black_overlay_indices()
    serialized = b"".join(index.to_bytes(2, "little") for index in indices)
    assert len(indices) == 483
    assert hashlib.sha256(serialized).hexdigest() == (
        "c4e91ab7acd1d0d03278393e4aeb8366d5bbfbb5704f87b0b63d0d7c59cc3c1d"
    )


def test_red_physically_masks_the_stock_black_overlay() -> None:
    red = (PixelColor.RED,) * PIXEL_COUNT
    assert stock_effective_pixels(red) == red


def test_compact_renderer_is_native_deterministic_and_reserves_unsafe_detail() -> None:
    entities = (
        EntityState("static.power", "Power", "42", "W", True),
        EntityState("static.temp", "Temperature", "21.5", "C", True),
        EntityState("static.status", "Status", "Ready", "", True),
    )
    first = render_compact_pixels(title="Overview", entities=entities)
    second = render_compact_pixels(title="Overview", entities=entities)
    assert first == second
    assert set(first) == {PixelColor.WHITE, PixelColor.BLACK, PixelColor.RED}

    for y in STOCK_OVERLAY_ROWS:
        for row in range(y, y + STOCK_OVERLAY_HEIGHT):
            start = row * 128 + STOCK_OVERLAY_X
            assert first[start : start + STOCK_OVERLAY_WIDTH] == (
                PixelColor.WHITE,
            ) * STOCK_OVERLAY_WIDTH

    preview = render_compact_preview(title="Overview", entities=entities)
    assert preview == render_compact_preview(title="Overview", entities=entities)
    with Image.open(BytesIO(preview)) as image:
        assert image.size == (128, 296)
        assert image.mode == "P"
        assert set(image.tobytes()) == {0, 1, 2}


def test_quantizer_has_exact_palette_and_red_precedence() -> None:
    image = Image.new("RGB", (128, 296), (255, 255, 255))
    image.putpixel((0, 0), (0, 0, 0))
    image.putpixel((1, 0), (204, 0, 0))
    pixels = quantize_image(image)
    assert pixels[0] is PixelColor.BLACK
    assert pixels[1] is PixelColor.RED
    assert pixels[2] is PixelColor.WHITE


def test_studio_preview_uses_compact_renderer_and_rejects_wrong_geometry(
    tmp_path,
) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        studio = client.get("/api/v1/studio").json()
        profile = next(item for item in studio["profiles"] if item["name"] == "default")
        preview = client.post(
            "/api/v1/studio/preview",
            json={
                "model": "TOP52810M-D01",
                "width": 128,
                "height": 296,
                "profile": profile,
            },
        )
        wrong_size = client.post(
            "/api/v1/studio/preview",
            json={
                "model": "TOP52810M-D01",
                "width": 129,
                "height": 296,
                "profile": profile,
            },
        )

    assert preview.status_code == 200, preview.text
    assert preview.headers["x-flexdisplay-preview-renderer"] == (
        "top52810-stock-preview"
    )
    assert preview.headers["x-flexdisplay-preview-constraint"] == (
        "stock-firmware-black-plane-overlay"
    )
    with Image.open(BytesIO(preview.content)) as image:
        assert image.size == (128, 296)
    assert wrong_size.status_code == 409


def test_top52810_profile_cannot_fall_through_to_receiver_or_custom_profile(
    tmp_path,
) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        receiver = client.get(
            "/api/v1/screen",
            headers={
                "X-FlexDisplay-ID": "TOP-PREVIEW01",
                "X-FlexDisplay-Model": "TOP52810M-D01",
                "X-FlexDisplay-Width": "128",
                "X-FlexDisplay-Height": "296",
            },
        )
        custom = client.put(
            "/api/v1/display-profiles/unsafe_top",
            json={
                "model": "TOP52810M-D01",
                "display_name": "Unsafe TOP override",
                "technology": "color",
                "width": 360,
                "height": 360,
                "shape": "round",
                "pixel_format": "RGB565",
                "color_depth": 16,
                "touch": False,
                "lvgl": True,
                "display_controller": "TEST",
            },
        )

    assert receiver.status_code == 409
    assert "preview only" in receiver.json()["detail"]
    assert "x-flexdisplay-image-sha256" not in receiver.headers
    assert app.state.store.get("TOP-PREVIEW01") is None
    assert custom.status_code == 400
