from __future__ import annotations

import math
import secrets
import threading
import time
from copy import deepcopy
from typing import Any, Iterable

from .home_assistant import EntityState

ALLOWED_ACTION_SERVICES: dict[str, set[str]] = {
    "homeassistant.toggle": {"light", "switch", "input_boolean"},
    "light.turn_on": {"light"},
    "light.turn_off": {"light"},
    "switch.turn_on": {"switch"},
    "switch.turn_off": {"switch"},
    "scene.turn_on": {"scene"},
    "cover.open_cover": {"cover"},
    "cover.close_cover": {"cover"},
    "cover.stop_cover": {"cover"},
}
ALLOWED_ACTION_DATA: dict[str, set[str]] = {
    "light.turn_on": {
        "brightness",
        "brightness_pct",
        "color_temp_kelvin",
        "rgb_color",
        "transition",
    },
    "light.turn_off": {"transition"},
}


class RookInteractionError(ValueError):
    """Raised when a receiver interaction or notification is unsafe."""


def round_tile_bounds(width: int, height: int, count: int) -> list[dict[str, int]]:
    """Return the exact circular-dashboard tile rectangles used by the renderer."""
    selected = max(0, min(4, count))
    if not selected:
        return []
    columns = 1 if selected == 1 else 2
    rows = math.ceil(selected / columns)
    left = 58
    right = width - 58
    top = 118
    bottom = height - 74
    gap = 9
    card_width = (right - left - gap * (columns - 1)) // columns
    card_height = (bottom - top - gap * (rows - 1)) // rows
    result: list[dict[str, int]] = []
    for index in range(selected):
        column = index % columns
        row = index // columns
        x0 = left + column * (card_width + gap)
        y0 = top + row * (card_height + gap)
        x1 = x0 + card_width
        y1 = y0 + card_height
        if selected == 3 and index == 2:
            x0, x1 = left + card_width // 2, right - card_width // 2
        result.append({"left": x0, "top": y0, "right": x1, "bottom": y1})
    return result


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0].lower() if "." in entity_id else ""


def _validated_action(raw: dict[str, Any], *, default_id: str) -> dict[str, Any]:
    service = str(raw.get("service") or "").strip().lower()
    entity_id = str(raw.get("entity_id") or "").strip().lower()
    domain = _domain(entity_id)
    if service not in ALLOWED_ACTION_SERVICES:
        raise RookInteractionError(f"Home Assistant service {service or '(empty)'} is not allowed")
    if domain not in ALLOWED_ACTION_SERVICES[service]:
        raise RookInteractionError(f"{service} cannot target {entity_id or '(empty)'}")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    if len(str(data)) > 2048:
        raise RookInteractionError("Action data is too large")
    unexpected = set(data) - ALLOWED_ACTION_DATA.get(service, set())
    if unexpected:
        raise RookInteractionError(
            f"{service} action data contains unsupported fields: {', '.join(sorted(unexpected))}"
        )
    confirmation = bool(raw.get("confirmation")) or service == "cover.open_cover"
    label = str(raw.get("label") or entity_id).replace("\n", " ").strip()[:40]
    return {
        "id": str(raw.get("id") or default_id)[:64],
        "label": label or "Action",
        "service": service,
        "entity_id": entity_id,
        "data": deepcopy(data),
        "confirmation": confirmation,
        "confirmation_text": str(
            raw.get("confirmation_text")
            or (f"Open {label}?" if service == "cover.open_cover" else f"Run {label}?")
        ).replace("\n", " ").strip()[:96],
    }


def default_entity_action(entity: EntityState, index: int) -> dict[str, Any] | None:
    """Build the bounded default action for a dashboard entity."""
    if not entity.available:
        return None
    domain = _domain(entity.entity_id)
    state = entity.state.strip().lower()
    if domain in {"light", "switch", "input_boolean"}:
        service = "homeassistant.toggle"
    elif domain == "scene":
        service = "scene.turn_on"
    elif domain == "cover":
        service = "cover.close_cover" if state in {"open", "opening"} else "cover.open_cover"
    else:
        return None
    return _validated_action(
        {
            "id": f"tile-{index + 1}",
            "label": entity.label,
            "service": service,
            "entity_id": entity.entity_id,
        },
        default_id=f"tile-{index + 1}",
    )


def build_page_interactions(
    entities: Iterable[EntityState], width: int, height: int
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    values = list(entities)[:4]
    bounds = round_tile_bounds(width, height, len(values))
    public: list[dict[str, Any]] = []
    private: dict[str, dict[str, Any]] = {}
    for index, entity in enumerate(values):
        action = default_entity_action(entity, index)
        if not action:
            continue
        action_id = action["id"]
        private[action_id] = action
        public.append(
            {
                "id": action_id,
                "label": action["label"],
                "entity_id": action["entity_id"],
                "bounds": bounds[index],
                "gesture": "hold" if action["confirmation"] else "tap",
                "confirmation": action["confirmation"],
                "confirmation_text": action["confirmation_text"],
            }
        )
    return public, private


def normalize_notification_actions(raw: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if raw in (None, ""):
        return [], {}
    if not isinstance(raw, list) or len(raw) > 3:
        raise RookInteractionError("Notifications may contain at most three actions")
    public: list[dict[str, Any]] = []
    private: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RookInteractionError("Notification actions must be objects")
        action = _validated_action(item, default_id=f"action-{index + 1}")
        action_id = f"action-{index + 1}"
        action["id"] = action_id
        private[action_id] = action
        public.append(
            {
                "id": action_id,
                "label": action["label"],
                "confirmation": action["confirmation"],
                "confirmation_text": action["confirmation_text"],
            }
        )
    return public, private


class RookBroker:
    """Thread-safe, in-memory receiver action maps and near-real-time alerts."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._interactions: dict[str, dict[str, Any]] = {}
        self._notifications: dict[str, dict[str, Any]] = {}
        self._sequence: dict[str, int] = {}

    def set_interactions(
        self,
        device_id: str,
        page_title: str,
        public: list[dict[str, Any]],
        private: dict[str, dict[str, Any]],
    ) -> None:
        with self._condition:
            previous = self._interactions.get(device_id) or {}
            revision = int(previous.get("revision") or 0) + 1
            self._interactions[device_id] = {
                "revision": revision,
                "page_title": page_title,
                "interactions": deepcopy(public),
                "actions": deepcopy(private),
            }

    def interactions(self, device_id: str) -> dict[str, Any]:
        with self._condition:
            value = self._interactions.get(device_id) or {
                "revision": 0,
                "page_title": "",
                "interactions": [],
            }
            return {
                "revision": value["revision"],
                "page_title": value["page_title"],
                "interactions": deepcopy(value["interactions"]),
            }

    def interaction_action(self, device_id: str, action_id: str) -> dict[str, Any] | None:
        with self._condition:
            action = (self._interactions.get(device_id) or {}).get("actions", {}).get(action_id)
            return deepcopy(action) if action else None

    def publish_notification(
        self,
        device_id: str,
        *,
        title: str,
        message: str,
        chime: str,
        duration: int,
        image: bytes,
        image_media_type: str,
        public_actions: list[dict[str, Any]],
        private_actions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        with self._condition:
            sequence = self._next_sequence_locked(device_id)
            self._sequence[device_id] = sequence
            notification_id = secrets.token_urlsafe(12)
            public = {
                "id": notification_id,
                "title": title,
                "message": message,
                "chime": chime,
                "duration": duration,
                "has_image": bool(image),
                "actions": deepcopy(public_actions),
            }
            self._notifications[device_id] = {
                "sequence": sequence,
                "expires_at": time.monotonic() + duration,
                "public": public,
                "actions": deepcopy(private_actions),
                "image": image,
                "image_media_type": image_media_type,
            }
            self._condition.notify_all()
            return {"sequence": sequence, "notification": deepcopy(public)}

    def _expire_locked(self, device_id: str) -> None:
        current = self._notifications.get(device_id)
        if current and time.monotonic() >= float(current["expires_at"]):
            self._notifications.pop(device_id, None)
            self._sequence[device_id] = self._next_sequence_locked(device_id)

    def _next_sequence_locked(self, device_id: str) -> int:
        return max(self._sequence.get(device_id, 0) + 1, int(time.time() * 1000))

    def wait(self, device_id: str, after: int, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, min(30.0, timeout))
        with self._condition:
            while True:
                self._expire_locked(device_id)
                sequence = self._sequence.get(device_id, 0)
                if sequence > after:
                    current = self._notifications.get(device_id)
                    return {
                        "sequence": sequence,
                        "notification": deepcopy(current["public"]) if current else None,
                    }
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"sequence": sequence, "notification": None}
                self._condition.wait(remaining)

    def dismiss(self, device_id: str, notification_id: str) -> bool:
        with self._condition:
            current = self._notifications.get(device_id)
            if not current or current["public"]["id"] != notification_id:
                return False
            self._notifications.pop(device_id, None)
            self._sequence[device_id] = self._next_sequence_locked(device_id)
            self._condition.notify_all()
            return True

    def notification_image(self, device_id: str, notification_id: str) -> tuple[bytes, str] | None:
        with self._condition:
            self._expire_locked(device_id)
            current = self._notifications.get(device_id)
            if not current or current["public"]["id"] != notification_id or not current["image"]:
                return None
            return bytes(current["image"]), str(current["image_media_type"])

    def notification_action(
        self, device_id: str, notification_id: str, action_id: str
    ) -> dict[str, Any] | None:
        with self._condition:
            self._expire_locked(device_id)
            current = self._notifications.get(device_id)
            if not current or current["public"]["id"] != notification_id:
                return None
            action = current["actions"].get(action_id)
            return deepcopy(action) if action else None
