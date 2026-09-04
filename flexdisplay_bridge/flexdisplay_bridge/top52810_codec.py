"""Pure TOP52810M-D01 stock-firmware image and transfer-plan codec.

This module deliberately contains no Bluetooth imports, discovery, connection,
notification, or device-write path.  It only converts a complete logical
black/white/red canvas into deterministic wire bytes and an offline transfer
plan for validation by a separately authorized transport adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import hashlib
import json
from typing import Iterable


WIDTH = 128
HEIGHT = 296
BYTES_PER_ROW = WIDTH // 8
PLANE_BYTES = BYTES_PER_ROW * HEIGHT
PIXEL_COUNT = WIDTH * HEIGHT
ATT_VALUE_MAX = 244
DATA_PAYLOAD_MAX = ATT_VALUE_MAX - 3


class PixelColor(IntEnum):
    """Logical tri-colour values; red has physical precedence."""

    WHITE = 0
    BLACK = 1
    RED = 2


@dataclass(frozen=True)
class EncodedPlanes:
    """Controller-order and stock-firmware wire-order image planes."""

    black_controller: bytes
    red_controller: bytes
    black_wire: bytes
    red_wire: bytes


@dataclass(frozen=True)
class TransferFrame:
    sequence: int
    phase: str
    payload: bytes
    expected_notification: bytes | None = None

    def as_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            "sequence": self.sequence,
            "phase": self.phase,
            "frame_length": len(self.payload),
            "frame_hex": self.payload.hex(" ").upper(),
            "frame_sha256": _sha256(self.payload),
            "write_type": "with_response",
        }
        if self.expected_notification is not None:
            record["expected_notify_hex"] = self.expected_notification.hex(" ").upper()
        return record


@dataclass(frozen=True)
class TransferPlan:
    """Complete, ordered, offline representation of one stock transfer."""

    sid: int
    frames: tuple[TransferFrame, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": "offline_prepare_only",
            "device_io": False,
            "geometry": {
                "width": WIDTH,
                "height": HEIGHT,
                "bytes_per_row": BYTES_PER_ROW,
            },
            "sid": f"{self.sid:06X}",
            "att_value_max": ATT_VALUE_MAX,
            "data_payload_max": DATA_PAYLOAD_MAX,
            "plane_bytes": PLANE_BYTES,
            "black_data_frame_count": sum(frame.phase == "black_data" for frame in self.frames),
            "red_data_frame_count": sum(frame.phase == "red_data" for frame in self.frames),
            "total_write_count": len(self.frames),
            "status_failure_hex": "30 31",
            "frames": [frame.as_record() for frame in self.frames],
        }

    def json_bytes(self) -> bytes:
        """Return the canonical, legacy-compatible transfer-plan document."""
        return (json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n").encode()

    @property
    def sha256(self) -> str:
        return _sha256(self.json_bytes())


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_plane(plane: bytes, label: str) -> bytes:
    if not isinstance(plane, bytes):
        raise TypeError(f"{label} plane must be bytes")
    if len(plane) != PLANE_BYTES:
        raise ValueError(f"{label} plane must be exactly {PLANE_BYTES} bytes")
    return plane


def reverse_bits(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFF:
        raise ValueError("byte value must be an integer between 0 and 255")
    value = ((value & 0xF0) >> 4) | ((value & 0x0F) << 4)
    value = ((value & 0xCC) >> 2) | ((value & 0x33) << 2)
    return ((value & 0xAA) >> 1) | ((value & 0x55) << 1)


def transform_plane(plane: bytes) -> bytes:
    """Transform between controller order and stock-firmware wire order.

    The transform reverses the 16 packed byte columns in each row, then
    reverses the bits in every byte.  It is its own inverse.
    """
    source = _require_plane(plane, "input")
    output = bytearray(PLANE_BYTES)
    for row_offset in range(0, PLANE_BYTES, BYTES_PER_ROW):
        row = source[row_offset : row_offset + BYTES_PER_ROW]
        output[row_offset : row_offset + BYTES_PER_ROW] = bytes(
            reverse_bits(value) for value in reversed(row)
        )
    return bytes(output)


def encode_pixels(pixels: Iterable[PixelColor | int]) -> EncodedPlanes:
    """Encode exactly one 128 x 296 logical canvas into two wire planes."""
    values = tuple(pixels)
    if len(values) != PIXEL_COUNT:
        raise ValueError(f"canvas must contain exactly {PIXEL_COUNT} pixels")

    colors: list[PixelColor] = []
    for index, value in enumerate(values):
        if isinstance(value, bool):
            raise ValueError(f"pixel {index} is not a valid TOP52810 colour")
        try:
            colors.append(PixelColor(value))
        except (TypeError, ValueError) as error:
            raise ValueError(f"pixel {index} is not a valid TOP52810 colour") from error

    black = bytearray(PLANE_BYTES)
    red = bytearray(PLANE_BYTES)
    for index in range(PLANE_BYTES):
        black_byte = 0
        red_byte = 0
        for bit in range(8):
            color = colors[index * 8 + bit]
            mask = 1 << (7 - bit)
            if color is not PixelColor.BLACK:
                black_byte |= mask
            if color is PixelColor.RED:
                red_byte |= mask
        black[index] = black_byte
        red[index] = red_byte

    black_controller = bytes(black)
    red_controller = bytes(red)
    return EncodedPlanes(
        black_controller=black_controller,
        red_controller=red_controller,
        black_wire=transform_plane(black_controller),
        red_wire=transform_plane(red_controller),
    )


def decode_wire_planes(black_wire: bytes, red_wire: bytes) -> tuple[PixelColor, ...]:
    """Decode wire planes for previews and round-trip validation."""
    black = transform_plane(_require_plane(black_wire, "black wire"))
    red = transform_plane(_require_plane(red_wire, "red wire"))
    pixels: list[PixelColor] = []
    for index in range(PLANE_BYTES):
        for bit in range(7, -1, -1):
            red_set = bool(red[index] & (1 << bit))
            black_set = bool(black[index] & (1 << bit))
            pixels.append(
                PixelColor.RED if red_set else (PixelColor.WHITE if black_set else PixelColor.BLACK)
            )
    return tuple(pixels)


def _command_frame(command: int, payload: bytes = b"") -> bytes:
    if not isinstance(command, int) or isinstance(command, bool) or not 0 <= command <= 99:
        raise ValueError("command must be an integer between 0 and 99")
    if not isinstance(payload, bytes):
        raise TypeError("command payload must be bytes")
    frame = f"{command:02d}".encode("ascii") + bytes((3 + len(payload),)) + payload
    if len(frame) > 255 or frame[2] != len(frame):
        raise ValueError("frame length is not representable in its length byte")
    return frame


def _data_frames(plane: bytes) -> tuple[bytes, ...]:
    source = _require_plane(plane, "wire")
    return tuple(
        _command_frame(30, source[offset : offset + DATA_PAYLOAD_MAX])
        for offset in range(0, PLANE_BYTES, DATA_PAYLOAD_MAX)
    )


def build_transfer_plan(sid: int, black_wire: bytes, red_wire: bytes) -> TransferPlan:
    """Build the exact 44-write plan used by the verified stock firmware."""
    if not isinstance(sid, int) or isinstance(sid, bool) or not 0 <= sid <= 0xFFFFFF:
        raise ValueError("SID must be a 24-bit integer")
    black_frames = _data_frames(black_wire)
    red_frames = _data_frames(red_wire)
    records: list[TransferFrame] = []

    def add(phase: str, payload: bytes, expected: bytes | None = None) -> None:
        records.append(TransferFrame(len(records) + 1, phase, payload, expected))

    add("session", _command_frame(3, sid.to_bytes(4, "little")), bytes.fromhex("30 34 00 00 00 00"))
    add("prepare_black", _command_frame(10), bytes.fromhex("31 31"))
    for frame in black_frames:
        add("black_data", frame)
    add("prepare_red", _command_frame(20), bytes.fromhex("32 31"))
    for frame in red_frames:
        add("red_data", frame)
    add("refresh", _command_frame(40), bytes.fromhex("34 31"))

    plan = TransferPlan(sid=sid, frames=tuple(records))
    if len(black_frames) != 20 or len(red_frames) != 20 or len(plan.frames) != 44:
        raise AssertionError("TOP52810 transfer plan invariant violated")
    return plan
