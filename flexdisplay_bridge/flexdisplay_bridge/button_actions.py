from __future__ import annotations

import json
import re
from typing import Any

BUTTONS = ("confirm", "left", "right", "up", "down")
GESTURES = ("short", "double", "long")
MODE = "home_assistant"
NAVIGATION_COMMANDS = ("next", "previous", "overview", "refresh")
ACTION_TYPES = ("none", "navigation", "home_assistant")

SERVICE_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
ENTITY_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
BLOCKED_SERVICE_DOMAINS = {"hassio", "supervisor", "shell_command"}
BLOCKED_SERVICES = {
    "homeassistant.restart",
    "homeassistant.stop",
    "homeassistant.reload_all",
}
MAX_MAPPINGS = len(BUTTONS) * len(GESTURES)
MAX_SERVICE_DATA_BYTES = 4096


class ButtonActionValidationError(ValueError):
    """Raised when a physical-button action mapping is unsafe or malformed."""


def mapping_key(button: str, gesture: str, mode: str = MODE) -> str:
    return f"{mode}:{button}:{gesture}"


def default_action(button: str, gesture: str, mode: str = MODE) -> dict[str, Any]:
    """Return the compatibility-preserving action for an unmapped gesture."""
    if mode == MODE and gesture == "short":
        if button in {"right", "down"}:
            return {"type": "navigation", "command": "next", "source": "default"}
        if button in {"left", "up"}:
            return {"type": "navigation", "command": "previous", "source": "default"}
    return {"type": "none", "source": "default"}


def resolve_action(
    mappings: dict[str, dict[str, Any]],
    button: str,
    gesture: str,
    mode: str,
) -> dict[str, Any]:
    if mode == MODE and button == "confirm" and gesture == "long":
        return {"type": "none", "source": "reserved_quick_menu"}
    configured = mappings.get(mapping_key(button, gesture, mode))
    return {**configured, "source": "configured"} if configured else default_action(button, gesture, mode)


def _normalized_action(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ButtonActionValidationError("Each mapping requires an action object")
    action_type = str(raw.get("type") or "")
    if action_type not in ACTION_TYPES:
        raise ButtonActionValidationError("Unsupported button action type")
    if action_type == "none":
        return {"type": "none"}
    if action_type == "navigation":
        command = str(raw.get("command") or "")
        if command not in NAVIGATION_COMMANDS:
            raise ButtonActionValidationError("Unsupported navigation command")
        return {"type": "navigation", "command": command}

    service = str(raw.get("service") or "").lower()
    entity_id = str(raw.get("entity_id") or "").lower()
    data = raw.get("data") or {}
    if not SERVICE_PATTERN.fullmatch(service):
        raise ButtonActionValidationError("Home Assistant service must use domain.service")
    if service.split(".", 1)[0] in BLOCKED_SERVICE_DOMAINS or service in BLOCKED_SERVICES:
        raise ButtonActionValidationError("That administrative Home Assistant service is not allowed")
    if entity_id and not ENTITY_PATTERN.fullmatch(entity_id):
        raise ButtonActionValidationError("Target entity must use domain.object_id")
    if not isinstance(data, dict):
        raise ButtonActionValidationError("Service data must be a JSON object")
    try:
        encoded = json.dumps(data, separators=(",", ":"))
    except (TypeError, ValueError) as err:
        raise ButtonActionValidationError("Service data must be valid JSON") from err
    if len(encoded.encode("utf-8")) > MAX_SERVICE_DATA_BYTES:
        raise ButtonActionValidationError("Service data is larger than 4 KB")
    normalized: dict[str, Any] = {"type": "home_assistant", "service": service}
    if entity_id:
        normalized["entity_id"] = entity_id
    if data:
        normalized["data"] = data
    return normalized


def normalize_mappings(payload: Any) -> dict[str, dict[str, Any]]:
    """Validate the API list and return its compact persisted representation."""
    raw_mappings = payload.get("mappings") if isinstance(payload, dict) else None
    if not isinstance(raw_mappings, list):
        raise ButtonActionValidationError("mappings must be a list")
    if len(raw_mappings) > MAX_MAPPINGS:
        raise ButtonActionValidationError(f"A device can have at most {MAX_MAPPINGS} mappings")

    result: dict[str, dict[str, Any]] = {}
    for raw in raw_mappings:
        if not isinstance(raw, dict):
            raise ButtonActionValidationError("Each mapping must be an object")
        button = str(raw.get("button") or "")
        gesture = str(raw.get("gesture") or "")
        mode = str(raw.get("mode") or MODE)
        if button not in BUTTONS:
            raise ButtonActionValidationError("Back and Power are reserved; choose Confirm or a direction button")
        if gesture not in GESTURES:
            raise ButtonActionValidationError("Gesture must be short, double, or long")
        if button == "confirm" and gesture == "long":
            raise ButtonActionValidationError(
                "Long Confirm is reserved for the FlexDisplay Quick Menu"
            )
        if mode != MODE:
            raise ButtonActionValidationError("Remote actions are currently limited to Home Assistant mode")
        key = mapping_key(button, gesture, mode)
        if key in result:
            raise ButtonActionValidationError("A button gesture can only be mapped once")
        result[key] = _normalized_action(raw.get("action"))
    return result


def mappings_payload(mappings: Any) -> list[dict[str, Any]]:
    """Convert persisted mappings into a stable API list."""
    if not isinstance(mappings, dict):
        return []
    result: list[dict[str, Any]] = []
    for button in BUTTONS:
        for gesture in GESTURES:
            key = mapping_key(button, gesture)
            action = mappings.get(key)
            if isinstance(action, dict):
                result.append(
                    {
                        "mode": MODE,
                        "button": button,
                        "gesture": gesture,
                        "action": action,
                    }
                )
    return result
