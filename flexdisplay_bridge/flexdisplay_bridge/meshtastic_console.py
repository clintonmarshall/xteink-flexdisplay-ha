from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .flexhub_client import FlexHubClient, FlexHubClientError

ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
MAX_TEMPLATES = 24
MAX_RULES = 24
MAX_RULE_DEVICES = 32


class MeshtasticConsoleValidationError(ValueError):
    """Raised when a saved console template or rule is malformed."""


def _bounded(value: Any, maximum: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:maximum]


def _boolean(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        selected = value.strip().lower()
        if selected in {"1", "true", "yes", "on"}:
            return True
        if selected in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return value == 1
    return fallback


def _item_id(value: Any, label: str) -> str:
    selected = _bounded(value, 48)
    if not ITEM_ID_PATTERN.fullmatch(selected):
        raise MeshtasticConsoleValidationError(
            f"{label} IDs may contain only letters, numbers, underscores, and hyphens"
        )
    return selected


def _template(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MeshtasticConsoleValidationError(
            f"Template {index + 1} must be an object"
        )
    template_id = _item_id(raw.get("id") or f"reply-{index + 1}", "Template")
    try:
        message = FlexHubClient.normalize_meshtastic_message(
            {**raw, "request_ack": _boolean(raw.get("request_ack"), False)}
        )
    except FlexHubClientError as exc:
        raise MeshtasticConsoleValidationError(str(exc)) from exc
    return {
        "id": template_id,
        "label": _bounded(raw.get("label") or template_id, 64),
        **message,
    }


def _rule(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MeshtasticConsoleValidationError(f"Rule {index + 1} must be an object")
    rule_id = _item_id(raw.get("id") or f"rule-{index + 1}", "Rule")
    prefix = _bounded(raw.get("match_prefix"), 80)
    if not prefix:
        raise MeshtasticConsoleValidationError(
            f"Rule {index + 1} needs a message prefix to match"
        )
    notify = _boolean(raw.get("notify"), False)
    notify_service = _bounded(raw.get("notify_service") or "notify.notify", 96)
    if notify and not re.fullmatch(r"notify\.[A-Za-z0-9_]+", notify_service):
        raise MeshtasticConsoleValidationError(
            f"Rule {index + 1} notification service must start with notify."
        )
    raw_devices = raw.get("device_ids") or []
    if not isinstance(raw_devices, list):
        raise MeshtasticConsoleValidationError(
            f"Rule {index + 1} display targets must be a list"
        )
    if len(raw_devices) > MAX_RULE_DEVICES:
        raise MeshtasticConsoleValidationError(
            f"Rule {index + 1} may target no more than {MAX_RULE_DEVICES} displays"
        )
    devices: list[str] = []
    for value in raw_devices[:MAX_RULE_DEVICES]:
        device_id = _bounded(value, 64)
        if not DEVICE_ID_PATTERN.fullmatch(device_id):
            raise MeshtasticConsoleValidationError(
                f"Rule {index + 1} contains an invalid display ID"
            )
        if device_id not in devices:
            devices.append(device_id)
    if not devices and not notify:
        raise MeshtasticConsoleValidationError(
            f"Rule {index + 1} must target a display or Home Assistant notification"
        )
    priority = _bounded(raw.get("priority") or "important", 16).lower()
    if priority not in {"normal", "important", "critical"}:
        raise MeshtasticConsoleValidationError(
            f"Rule {index + 1} has an unsupported priority"
        )
    channel = raw.get("channel")
    if channel in {None, "", "all"}:
        selected_channel = None
    else:
        try:
            selected_channel = int(channel)
        except (TypeError, ValueError) as exc:
            raise MeshtasticConsoleValidationError(
                f"Rule {index + 1} channel must be a number"
            ) from exc
        if not 0 <= selected_channel <= 7:
            raise MeshtasticConsoleValidationError(
                f"Rule {index + 1} channel must be between 0 and 7"
            )
    node = _bounded(raw.get("node"), 16).lower()
    if node:
        if not re.fullmatch(r"!?[0-9a-f]{8}", node):
            raise MeshtasticConsoleValidationError(
                f"Rule {index + 1} node must contain eight hexadecimal digits"
            )
        node = node if node.startswith("!") else f"!{node}"
    return {
        "id": rule_id,
        "label": _bounded(raw.get("label") or rule_id, 64),
        "enabled": _boolean(raw.get("enabled"), True),
        "match_prefix": prefix,
        "case_sensitive": _boolean(raw.get("case_sensitive"), False),
        "channel": selected_channel,
        "node": node,
        "device_ids": devices,
        "title": _bounded(raw.get("title") or "MESHTASTIC ALERT", 80),
        "priority": priority,
        "strip_prefix": _boolean(raw.get("strip_prefix"), False),
        "notify": notify,
        "notify_service": notify_service,
    }


class MeshtasticConsoleStore:
    """Persist bounded quick replies and incoming-message display rules."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            payload = {}
        templates = payload.get("templates") if isinstance(payload, dict) else []
        rules = payload.get("rules") if isinstance(payload, dict) else []
        last_evaluation = (
            payload.get("last_evaluation") if isinstance(payload, dict) else {}
        )
        processed_keys = (
            payload.get("processed_keys") if isinstance(payload, dict) else []
        )
        valid_templates: list[dict[str, Any]] = []
        template_ids: set[str] = set()
        for index, raw in enumerate(
            templates[:MAX_TEMPLATES] if isinstance(templates, list) else []
        ):
            try:
                item = _template(raw, index)
            except MeshtasticConsoleValidationError:
                continue
            if item["id"] not in template_ids:
                template_ids.add(item["id"])
                valid_templates.append(item)
        valid_rules: list[dict[str, Any]] = []
        rule_ids: set[str] = set()
        for index, raw in enumerate(
            rules[:MAX_RULES] if isinstance(rules, list) else []
        ):
            try:
                item = _rule(raw, index)
            except MeshtasticConsoleValidationError:
                continue
            if item["id"] not in rule_ids:
                rule_ids.add(item["id"])
                valid_rules.append(item)
        return {
            "version": 1,
            "templates": valid_templates,
            "rules": valid_rules,
            "last_evaluation": last_evaluation
            if isinstance(last_evaluation, dict)
            else {},
            "processed_keys": (
                [str(value)[:180] for value in processed_keys[-256:]]
                if isinstance(processed_keys, list)
                else []
            ),
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.path)

    def payload(self) -> dict[str, Any]:
        with self._lock:
            result = json.loads(json.dumps(self._data))
            result.pop("processed_keys", None)
            return result

    def replace(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_templates = payload.get("templates") or []
        raw_rules = payload.get("rules") or []
        if not isinstance(raw_templates, list) or len(raw_templates) > MAX_TEMPLATES:
            raise MeshtasticConsoleValidationError(
                f"No more than {MAX_TEMPLATES} quick replies may be saved"
            )
        if not isinstance(raw_rules, list) or len(raw_rules) > MAX_RULES:
            raise MeshtasticConsoleValidationError(
                f"No more than {MAX_RULES} incoming-message rules may be saved"
            )
        templates = [_template(raw, index) for index, raw in enumerate(raw_templates)]
        rules = [_rule(raw, index) for index, raw in enumerate(raw_rules)]
        if len({item["id"] for item in templates}) != len(templates):
            raise MeshtasticConsoleValidationError("Quick reply IDs must be unique")
        if len({item["id"] for item in rules}) != len(rules):
            raise MeshtasticConsoleValidationError("Rule IDs must be unique")
        with self._lock:
            self._data["templates"] = templates
            self._data["rules"] = rules
            self._save()
            return self.payload()

    @staticmethod
    def _message_key(message: dict[str, Any]) -> str:
        sequence = message.get("sequence")
        if sequence is not None:
            session = message.get("session_id") or message.get("boot_id") or ""
            packet = message.get("packet_id") or message.get("id") or ""
            received = message.get("received_at") or message.get("timestamp") or ""
            delivery = message.get("delivery_state") or message.get("status") or ""
            return f"sequence:{session}:{sequence}:{packet}:{received}:{delivery}"
        packet_id = message.get("packet_id") or message.get("id")
        if packet_id is not None:
            return f"packet:{packet_id}:{message.get('direction', '')}"
        return "message:" + json.dumps(message, sort_keys=True, default=str)[:512]

    def claim_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Atomically claim unseen messages so rules are not replayed after restart."""
        with self._lock:
            keys = list(self._data.get("processed_keys") or [])
            seen = set(keys)
            claimed: list[dict[str, Any]] = []
            for message in messages:
                key = self._message_key(message)
                if key in seen:
                    continue
                seen.add(key)
                keys.append(key)
                claimed.append(dict(message))
            if claimed:
                self._data["processed_keys"] = keys[-256:]
                self._save()
            return claimed

    def matching_rules(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        direction = str(message.get("direction") or "inbound").lower()
        if direction in {"outbound", "tx", "sent"}:
            return []
        text = str(message.get("text") or "")
        try:
            channel = int(message.get("channel", 0))
        except (TypeError, ValueError):
            channel = 0
        raw_node = (
            message.get("sender_id")
            or message.get("sender")
            or message.get("from_id")
            or message.get("node_id")
            or message.get("from")
            or ""
        )
        if isinstance(raw_node, int):
            node = f"!{raw_node:08x}"
        else:
            node = str(raw_node).strip().lower()
            if re.fullmatch(r"[0-9a-f]{8}", node):
                node = f"!{node}"
        with self._lock:
            matches: list[dict[str, Any]] = []
            for configured in self._data["rules"]:
                rule = dict(configured)
                if not rule.get("enabled", True):
                    continue
                expected_channel = rule.get("channel")
                if expected_channel is not None and expected_channel != channel:
                    continue
                if rule.get("node") and rule["node"] != node:
                    continue
                prefix = str(rule.get("match_prefix") or "")
                candidate = text if rule.get("case_sensitive") else text.casefold()
                wanted = prefix if rule.get("case_sensitive") else prefix.casefold()
                if candidate.startswith(wanted):
                    matches.append(rule)
            return matches

    def record_evaluation(
        self,
        message: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            self._data["last_evaluation"] = {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "sequence": message.get("sequence"),
                "packet_id": message.get("packet_id") or message.get("id"),
                "results": results[:MAX_RULE_DEVICES],
            }
            self._save()
