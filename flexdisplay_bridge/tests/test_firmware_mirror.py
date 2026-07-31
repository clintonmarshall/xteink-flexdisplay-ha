from __future__ import annotations

import hashlib

from flexdisplay_bridge.config import FirmwareConfig
from flexdisplay_bridge.firmware_mirror import FirmwareMirror


def test_packaged_firmware_seeds_verified_cache_without_network(tmp_path) -> None:
    payload = b"packaged-flexdisplay-firmware"
    packaged = tmp_path / "packaged.bin"
    packaged.write_bytes(payload)
    firmware = FirmwareConfig(
        version="1.5.0-flexdisplay.0.32.0",
        url="https://private.example.test/firmware.bin",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )
    mirror = FirmwareMirror(tmp_path / "cache", packaged)

    cached = mirror.prepare(firmware)

    assert cached.read_bytes() == payload
    assert mirror.status(firmware)["source"] == "packaged"


def test_mismatched_packaged_firmware_falls_back_to_download(
    tmp_path, monkeypatch
) -> None:
    packaged = tmp_path / "packaged.bin"
    packaged.write_bytes(b"wrong")
    payload = b"custom-firmware"
    firmware = FirmwareConfig(
        version="custom",
        url="https://example.test/custom.bin",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            assert chunk_size > 0
            yield payload

    monkeypatch.setattr(
        "flexdisplay_bridge.firmware_mirror.requests.get",
        lambda *_args, **_kwargs: Response(),
    )
    mirror = FirmwareMirror(tmp_path / "cache", packaged)

    cached = mirror.prepare(firmware)

    assert cached.read_bytes() == payload
    assert mirror.status(firmware)["source"] == "download"
