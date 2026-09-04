import hashlib

import pytest

from flexdisplay_bridge.top52810_codec import (
    ATT_VALUE_MAX,
    BYTES_PER_ROW,
    HEIGHT,
    PIXEL_COUNT,
    PLANE_BYTES,
    WIDTH,
    PixelColor,
    build_transfer_plan,
    decode_wire_planes,
    encode_pixels,
    transform_plane,
)


SID = 0xA1B2C3


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def solid(color: PixelColor) -> list[PixelColor]:
    return [color] * PIXEL_COUNT


@pytest.mark.parametrize(
    ("color", "black_hash", "red_hash", "plan_hash"),
    [
        (
            PixelColor.WHITE,
            "a3671594682c80e5f08721602dd0136dd1b6c099160ac98ffa6d5b2fca4ba9af",
            "373e58db31dbad517dfede6bb84a58f4f7d5bf03630597ca677658b8bd136106",
            "8f4e9e35d538f79c43097dc9af58fe2cd8ee340ab259acd1d82bd2755844fa27",
        ),
        (
            PixelColor.BLACK,
            "373e58db31dbad517dfede6bb84a58f4f7d5bf03630597ca677658b8bd136106",
            "373e58db31dbad517dfede6bb84a58f4f7d5bf03630597ca677658b8bd136106",
            "cb1af40cd8792f316d97399c36cb265b59a4797f541a086d7cf587f99658c8d1",
        ),
        (
            PixelColor.RED,
            "a3671594682c80e5f08721602dd0136dd1b6c099160ac98ffa6d5b2fca4ba9af",
            "a3671594682c80e5f08721602dd0136dd1b6c099160ac98ffa6d5b2fca4ba9af",
            "f53fe59fa70fd1f0bde610bc113279d429ea3e1138f58a5f9433daaa621af255",
        ),
    ],
)
def test_physically_verified_solid_color_goldens(
    color: PixelColor, black_hash: str, red_hash: str, plan_hash: str
) -> None:
    encoded = encode_pixels(solid(color))
    assert sha256(encoded.black_wire) == black_hash
    assert sha256(encoded.red_wire) == red_hash
    assert build_transfer_plan(SID, encoded.black_wire, encoded.red_wire).sha256 == plan_hash
    assert decode_wire_planes(encoded.black_wire, encoded.red_wire) == tuple(solid(color))


def diagnostic_pixels() -> list[PixelColor]:
    pixels = solid(PixelColor.WHITE)

    def set_pixel(x: int, y: int, color: PixelColor) -> None:
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            pixels[y * WIDTH + x] = color

    def rect(x0: int, y0: int, x1: int, y1: int, color: PixelColor) -> None:
        for y in range(y0, y1):
            for x in range(x0, x1):
                set_pixel(x, y, color)

    rect(0, 0, WIDTH, 4, PixelColor.BLACK)
    rect(0, HEIGHT - 4, WIDTH, HEIGHT, PixelColor.BLACK)
    rect(0, 0, 4, HEIGHT, PixelColor.BLACK)
    rect(WIDTH - 4, 0, WIDTH, HEIGHT, PixelColor.BLACK)
    rect(8, 10, 30, 32, PixelColor.BLACK)
    rect(WIDTH - 30, 10, WIDTH - 8, 32, PixelColor.RED)
    for dy in range(22):
        for dx in range(dy + 1):
            set_pixel(8 + dx, HEIGHT - 10 - dy, PixelColor.RED)
    cx, cy = WIDTH - 20, HEIGHT - 20
    for dy in range(-11, 12):
        span = 11 - abs(dy)
        for dx in range(-span, span + 1):
            set_pixel(cx + dx, cy + dy, PixelColor.BLACK)
    rect(8, 52, WIDTH - 8, 60, PixelColor.RED)
    rect(8, 72, WIDTH - 8, 80, PixelColor.BLACK)
    rect(WIDTH // 2 - 2, 92, WIDTH // 2 + 2, 142, PixelColor.BLACK)
    rect(WIDTH // 2 - 25, 115, WIDTH // 2 + 25, 119, PixelColor.RED)
    return pixels


def test_asymmetric_orientation_golden_prevents_mirroring_or_inversion() -> None:
    pixels = diagnostic_pixels()
    encoded = encode_pixels(pixels)
    assert sha256(encoded.black_wire) == "a47c99030031d3271f9d4d6d9f7a3d011f3a67a5392402779e42bacc40a872a3"
    assert sha256(encoded.red_wire) == "1954819e55f7fb03d40f17beb6be4e55b6eea3706bba7d2f1ae0d3be8ced769e"
    assert build_transfer_plan(SID, encoded.black_wire, encoded.red_wire).sha256 == (
        "9da514d391bfd40e444138f87d7aa8b06445633b2c37a22fa6a5969d11707876"
    )
    assert decode_wire_planes(encoded.black_wire, encoded.red_wire) == tuple(pixels)


def test_transform_is_row_local_and_involutive() -> None:
    source = bytes(range(BYTES_PER_ROW)) * HEIGHT
    transformed = transform_plane(source)
    expected_row = bytes(int(f"{value:08b}"[::-1], 2) for value in reversed(range(BYTES_PER_ROW)))
    assert transformed[:BYTES_PER_ROW] == expected_row
    assert transform_plane(transformed) == source


def test_exact_44_write_sequence_and_acknowledgements() -> None:
    encoded = encode_pixels(solid(PixelColor.WHITE))
    plan = build_transfer_plan(SID, encoded.black_wire, encoded.red_wire)
    assert ATT_VALUE_MAX == 244
    assert len(plan.frames) == 44
    assert [frame.phase for frame in plan.frames].count("black_data") == 20
    assert [frame.phase for frame in plan.frames].count("red_data") == 20
    assert plan.frames[0].payload == bytes.fromhex("30 33 07 C3 B2 A1 00")
    assert plan.frames[0].expected_notification == bytes.fromhex("30 34 00 00 00 00")
    assert plan.frames[1].payload == bytes.fromhex("31 30 03")
    assert plan.frames[1].expected_notification == bytes.fromhex("31 31")
    assert plan.frames[22].payload == bytes.fromhex("32 30 03")
    assert plan.frames[22].expected_notification == bytes.fromhex("32 31")
    assert plan.frames[-1].payload == bytes.fromhex("34 30 03")
    assert plan.frames[-1].expected_notification == bytes.fromhex("34 31")
    assert all(len(frame.payload) == 244 for frame in plan.frames[2:21])
    assert len(plan.frames[21].payload) == 160
    assert all(len(frame.payload) == 244 for frame in plan.frames[23:42])
    assert len(plan.frames[42].payload) == 160


@pytest.mark.parametrize("bad_length", [0, PLANE_BYTES - 1, PLANE_BYTES + 1])
def test_plane_length_validation_fails_closed(bad_length: int) -> None:
    with pytest.raises(ValueError, match="exactly 4736 bytes"):
        transform_plane(bytes(bad_length))


def test_canvas_and_color_validation_fail_closed() -> None:
    with pytest.raises(ValueError, match="exactly 37888 pixels"):
        encode_pixels([PixelColor.WHITE])
    invalid = solid(PixelColor.WHITE)
    invalid[123] = 3  # type: ignore[assignment]
    with pytest.raises(ValueError, match="pixel 123"):
        encode_pixels(invalid)
    invalid[123] = True  # type: ignore[assignment]
    with pytest.raises(ValueError, match="pixel 123"):
        encode_pixels(invalid)


@pytest.mark.parametrize("sid", [-1, 0x1000000, True, "A1B2C3"])
def test_sid_validation_fails_closed(sid: object) -> None:
    plane = bytes(PLANE_BYTES)
    with pytest.raises(ValueError, match="24-bit"):
        build_transfer_plan(sid, plane, plane)  # type: ignore[arg-type]
