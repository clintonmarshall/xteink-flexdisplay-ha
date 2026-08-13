from __future__ import annotations

import hashlib
import hmac
import re


LVGL_RECEIVER_KEY_CONTEXT = b"flexdisplay.lvgl-receiver-key.v1\x00"
_DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
_DERIVED_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ReceiverAuthError(ValueError):
    """Raised when a receiver credential cannot be safely derived."""


def derive_receiver_key(master: str, device_id: str, epoch: int = 0) -> str:
    """Derive the sole credential that may be provisioned to one receiver."""

    try:
        master_bytes = str(master).encode("utf-8", errors="strict")
        selected_id = str(device_id)
        device_id_bytes = selected_id.encode("ascii", errors="strict")
    except UnicodeError as err:
        raise ReceiverAuthError(
            "Receiver authentication input is not valid UTF-8/ASCII"
        ) from err
    if not 16 <= len(master_bytes) <= 256:
        raise ReceiverAuthError("Receiver key master must contain 16–256 UTF-8 bytes")
    if not _DEVICE_ID_PATTERN.fullmatch(selected_id) or selected_id != selected_id.upper():
        raise ReceiverAuthError("Receiver device ID must use its canonical uppercase form")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or not 0 <= epoch <= 0x7FFFFFFF:
        raise ReceiverAuthError("Receiver credential epoch is invalid")
    context = (
        LVGL_RECEIVER_KEY_CONTEXT
        if epoch == 0
        else LVGL_RECEIVER_KEY_CONTEXT + f"epoch:{epoch}\x00".encode("ascii")
    )
    return hmac.new(
        master_bytes,
        context + device_id_bytes,
        hashlib.sha256,
    ).hexdigest()


def verify_receiver_key(
    master: str,
    device_id: str,
    supplied: str | None,
    epoch: int = 0,
) -> bool:
    """Verify a receiver key without exposing the expected derived value."""

    selected = str(supplied or "")
    if not _DERIVED_KEY_PATTERN.fullmatch(selected):
        return False
    return hmac.compare_digest(selected, derive_receiver_key(master, device_id, epoch))
