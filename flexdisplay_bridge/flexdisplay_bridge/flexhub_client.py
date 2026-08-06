from __future__ import annotations

import json
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class FlexHubClientError(ValueError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class FlexHubClient:
    """Persist a FlexHub connection and expose its bounded status endpoint."""

    # Kept in sync with the memory-constrained SenseCAP FlexHub ring buffer.
    MESHTASTIC_MESSAGE_CAPACITY = 16

    def __init__(
        self,
        path: Path,
        *,
        default_url: str = "",
        default_access_pin: str = "",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.path = path
        self.timeout_seconds = max(1.0, min(15.0, timeout_seconds))
        self._lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._url = ""
        self._access_pin = ""
        self._status: dict[str, Any] = {}
        self._last_seen = ""
        self._error = ""
        self._meshtastic_console: dict[str, Any] = {
            "last_message": {},
            "last_sender": "",
            "last_channel": None,
            "unread_count": 0,
            "cursor": 0,
            "last_polled_at": "",
            "error": "",
        }
        self._meshtastic_seen: list[str] = []
        self._meshtastic_initialized = False
        self._last_meshtastic_send = 0.0
        self._load(default_url, default_access_pin)

    @staticmethod
    def _url_value(value: str) -> str:
        candidate = str(value or "").strip().rstrip("/")
        if not candidate:
            return ""
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise FlexHubClientError("FlexHub URL must start with http:// or https://")
        if (
            parsed.username
            or parsed.password
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise FlexHubClientError(
                "FlexHub URL cannot contain credentials, a query, or a fragment"
            )
        if len(candidate) > 240:
            raise FlexHubClientError("FlexHub URL is too long")
        normalized_path = parsed.path.rstrip("/")
        supported_paths = {"", "/flexhub", "/meshtastic", "/api/flexhub/status"}
        if normalized_path not in supported_paths:
            raise FlexHubClientError(
                "FlexHub address must be the hub base address, for example "
                "http://flexhub.local; only /flexhub, /meshtastic, or "
                "/api/flexhub/status may be pasted after it"
            )
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _route_urls(url: str) -> dict[str, str]:
        if not url:
            return {
                "selector_url": "",
                "console_url": "",
                "meshtastic_url": "",
                "status_url": "",
            }
        return {
            "selector_url": f"{url}/",
            "console_url": f"{url}/flexhub",
            "meshtastic_url": f"{url}/meshtastic",
            "status_url": f"{url}/api/flexhub/status",
        }

    def _load(self, default_url: str, default_access_pin: str) -> None:
        payload: dict[str, Any] = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (FileNotFoundError, OSError, ValueError):
            pass
        self._url = self._url_value(str(payload.get("url") or default_url or ""))
        self._access_pin = str(payload.get("access_pin") or default_access_pin or "")[
            :64
        ]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps({"url": self._url, "access_pin": self._access_pin}, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @property
    def configured(self) -> bool:
        with self._lock:
            return bool(self._url)

    def configure(self, url: str, access_pin: str = "") -> dict[str, Any]:
        normalized = self._url_value(url)
        with self._lock:
            self._url = normalized
            self._access_pin = str(access_pin or "")[:64]
            self._status = {}
            self._last_seen = ""
            self._error = "Not configured" if not normalized else "Waiting for FlexHub"
            self._meshtastic_seen = []
            self._meshtastic_initialized = False
            self._meshtastic_console = {
                "last_message": {},
                "last_sender": "",
                "last_channel": None,
                "unread_count": 0,
                "cursor": 0,
                "last_polled_at": "",
                "error": "",
            }
            self._last_meshtastic_send = 0.0
            self._save()
        return self.summary()

    def poll(self) -> dict[str, Any]:
        with self._lock:
            url = self._url
            access_pin = self._access_pin
        if not url:
            with self._lock:
                self._error = "Not configured"
            return self.summary()

        headers = {"Accept": "application/json"}
        if access_pin:
            headers["X-FlexHub-Token"] = access_pin
        status_url = self._route_urls(url)["status_url"]
        try:
            with self._request_lock:
                response = requests.get(
                    status_url,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                )
            if 300 <= response.status_code < 400:
                destination = str(
                    response.headers.get("Location") or "another address"
                )[:160]
                raise FlexHubClientError(
                    f"FlexHub status endpoint redirected to {destination}; "
                    "use the hub's direct http:// or https:// base address"
                )
            if response.status_code == 401:
                raise FlexHubClientError(
                    "FlexHub rejected the access PIN (HTTP 401); update the PIN in Studio"
                )
            if response.status_code == 404:
                raise FlexHubClientError(
                    "FlexHub status API was not found (HTTP 404); the address may be a "
                    "Meshtastic-only device or the hub needs FlexHub firmware"
                )
            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type") or "").lower()
            body_start = str(response.text or "").lstrip()[:32].lower()
            if "text/html" in content_type or body_start.startswith(
                ("<!doctype html", "<html")
            ):
                raise FlexHubClientError(
                    "A Meshtastic web page was reached, but the FlexHub status API is unavailable; "
                    "install FlexHub firmware or check the hub base address"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise FlexHubClientError(
                    "FlexHub returned a non-JSON status response; "
                    "check the hub address and firmware"
                ) from exc
            if not isinstance(payload, dict):
                raise FlexHubClientError("FlexHub returned an invalid status response")
        except (requests.RequestException, FlexHubClientError) as exc:
            with self._lock:
                self._error = str(exc)[:240]
            return self.summary()

        with self._lock:
            self._status = payload
            self._last_seen = datetime.now(UTC).isoformat(timespec="seconds")
            self._error = ""
        return self.summary()

    def _endpoint_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            url = self._url
            access_pin = self._access_pin
        if not url:
            raise FlexHubClientError("FlexHub is not configured")
        headers = {"Accept": "application/json"}
        if access_pin:
            headers["X-FlexHub-Token"] = access_pin
        request_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": self.timeout_seconds,
            "allow_redirects": False,
        }
        if params:
            request_kwargs["params"] = params
        if payload is not None:
            request_kwargs["json"] = payload
        try:
            with self._request_lock:
                if method == "GET":
                    response = requests.get(f"{url}{path}", **request_kwargs)
                else:
                    response = requests.post(f"{url}{path}", **request_kwargs)
        except requests.RequestException as exc:
            raise FlexHubClientError(f"FlexHub request failed: {exc}") from exc
        if 300 <= response.status_code < 400:
            raise FlexHubClientError("FlexHub API redirected unexpectedly")
        if response.status_code == 401:
            raise FlexHubClientError(
                "FlexHub rejected the access PIN (HTTP 401)", status_code=401
            )
        if response.status_code >= 400:
            detail = ""
            try:
                failure = response.json()
                if isinstance(failure, dict):
                    detail = str(failure.get("detail") or failure.get("error") or "")
            except ValueError:
                detail = str(response.text or "")
            detail = re.sub(r"\s+", " ", detail).strip()[:180]
            suffix = f": {detail}" if detail else ""
            raise FlexHubClientError(
                f"FlexHub API request failed (HTTP {response.status_code}){suffix}",
                status_code=response.status_code,
            )
        content_type = str(response.headers.get("Content-Type") or "").lower()
        body_start = str(response.text or "").lstrip()[:32].lower()
        if "text/html" in content_type or body_start.startswith(
            ("<!doctype html", "<html")
        ):
            raise FlexHubClientError("FlexHub returned a web page instead of JSON")
        try:
            result = response.json()
        except ValueError as exc:
            raise FlexHubClientError(
                "FlexHub returned an invalid JSON response"
            ) from exc
        if not isinstance(result, dict):
            raise FlexHubClientError("FlexHub returned an invalid JSON response")
        return result

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

    def observe_meshtastic_messages(
        self, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        raw_messages = payload.get("messages") or []
        if not isinstance(raw_messages, list):
            return []
        observed: list[dict[str, Any]] = []
        session_id = payload.get("session_id") or payload.get("boot_id")
        with self._lock:
            baseline = not self._meshtastic_initialized
            seen = set(self._meshtastic_seen)
            for raw in raw_messages:
                if not isinstance(raw, dict):
                    continue
                message = dict(raw)
                if session_id and not message.get("session_id"):
                    message["session_id"] = session_id
                key = self._message_key(message)
                if key in seen:
                    continue
                seen.add(key)
                self._meshtastic_seen.append(key)
                observed.append(message)
                direction = str(message.get("direction") or "inbound").lower()
                if not baseline and direction not in {"outbound", "tx", "sent"}:
                    self._meshtastic_console["unread_count"] = (
                        int(self._meshtastic_console.get("unread_count") or 0) + 1
                    )
                self._meshtastic_console["last_message"] = message
                self._meshtastic_console["last_sender"] = str(
                    message.get("sender")
                    or message.get("sender_name")
                    or message.get("from")
                    or message.get("from_id")
                    or ""
                )[:96]
                self._meshtastic_console["last_channel"] = message.get("channel")
                self._meshtastic_console["last_message_at"] = str(
                    message.get("received_at")
                    or message.get("timestamp")
                    or message.get("time")
                    or datetime.now(UTC).isoformat(timespec="seconds")
                )[:64]
                try:
                    sequence = int(message.get("sequence") or 0)
                except (TypeError, ValueError):
                    sequence = 0
                self._meshtastic_console["cursor"] = max(
                    int(self._meshtastic_console.get("cursor") or 0), sequence
                )
            if len(self._meshtastic_seen) > 256:
                self._meshtastic_seen = self._meshtastic_seen[-256:]
            self._meshtastic_initialized = True
        # The first successful poll establishes a baseline for retained hub
        # history. Keep it visible in the inbox and summary, but do not replay
        # old records into MQTT, notifications, or display-routing rules.
        return [] if baseline else observed

    def fetch_messages(
        self,
        *,
        after: int = 0,
        limit: int = 30,
        session_id: int | None = None,
        query: str = "",
        direction: str = "",
        channel: int | None = None,
        node: str = "",
        observe: bool = True,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        after = max(0, min(0xFFFFFFFF, int(after)))
        limit = max(1, min(self.MESHTASTIC_MESSAGE_CAPACITY, int(limit)))
        query = str(query or "").strip()
        try:
            query_bytes = query.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise FlexHubClientError(
                "Meshtastic message search is not valid UTF-8"
            ) from exc
        if len(query_bytes) > 80:
            raise FlexHubClientError(
                "Meshtastic message search is limited to 80 UTF-8 bytes"
            )
        direction = str(direction or "").strip().lower()
        if direction not in {"", "all", "inbound", "outbound"}:
            raise FlexHubClientError("Meshtastic direction is not supported")
        node = str(node or "").strip()
        if node and not re.fullmatch(r"!?[0-9A-Fa-f]{8}", node):
            raise FlexHubClientError(
                "Meshtastic node IDs must contain eight hexadecimal digits"
            )
        params: dict[str, Any] = {"after": after, "limit": limit}
        if session_id is not None:
            selected_session = int(session_id)
            if not 0 <= selected_session <= 0xFFFFFFFF:
                raise FlexHubClientError(
                    "Meshtastic session ID is outside the valid range"
                )
            params["session_id"] = selected_session
        if query:
            params["query"] = query
        if direction and direction != "all":
            params["direction"] = direction
        if channel is not None:
            selected_channel = int(channel)
            if not 0 <= selected_channel <= 7:
                raise FlexHubClientError("Meshtastic channel must be between 0 and 7")
            params["channel"] = selected_channel
        if node:
            params["node"] = node if node.startswith("!") else f"!{node}"
        try:
            result = self._endpoint_json(
                "GET", "/api/flexhub/meshtastic/messages", params=params
            )
        except FlexHubClientError as exc:
            with self._lock:
                self._meshtastic_console["error"] = str(exc)[:180]
            raise
        with self._lock:
            self._meshtastic_console["last_polled_at"] = datetime.now(UTC).isoformat(
                timespec="seconds"
            )
            self._meshtastic_console["error"] = ""
        return result, self.observe_meshtastic_messages(result) if observe else []

    def messages(self, **filters: Any) -> dict[str, Any]:
        result, _ = self.fetch_messages(**filters)
        return result

    def meshtastic_nodes(self) -> dict[str, Any]:
        return self._endpoint_json("GET", "/api/flexhub/meshtastic/nodes")

    @staticmethod
    def normalize_meshtastic_message(payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise FlexHubClientError("Meshtastic message text is required")
        if any(
            ord(character) < 0x20 and character not in "\t\n\r" for character in text
        ):
            raise FlexHubClientError(
                "Meshtastic message text contains an invalid control character"
            )
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise FlexHubClientError(
                "Meshtastic message text is not valid UTF-8"
            ) from exc
        if len(encoded) > 220:
            raise FlexHubClientError(
                "Meshtastic messages are limited to 220 UTF-8 bytes"
            )
        destination = str(payload.get("destination") or "broadcast").strip().lower()
        if destination != "broadcast":
            if not re.fullmatch(r"!?[0-9a-f]{8}", destination):
                raise FlexHubClientError(
                    "Meshtastic destination must be broadcast or an eight-digit node ID"
                )
            destination = (
                destination if destination.startswith("!") else f"!{destination}"
            )
            if destination in {"!00000000", "!ffffffff"}:
                raise FlexHubClientError(
                    "Meshtastic destination uses a reserved node ID"
                )
        try:
            channel = int(payload.get("channel", 0))
        except (TypeError, ValueError) as exc:
            raise FlexHubClientError("Meshtastic channel must be a number") from exc
        if not 0 <= channel <= 7:
            raise FlexHubClientError("Meshtastic channel must be between 0 and 7")
        return {
            "text": text,
            "destination": destination,
            "channel": channel,
            "request_ack": payload.get("request_ack") is True
            if destination != "broadcast"
            else False,
        }

    def send_meshtastic_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self.normalize_meshtastic_message(payload)
        now = time.monotonic()
        with self._lock:
            if now - self._last_meshtastic_send < 1.0:
                raise FlexHubClientError(
                    "Wait one second before sending another Meshtastic message",
                    status_code=429,
                )
            self._last_meshtastic_send = now
        return self._endpoint_json(
            "POST", "/api/flexhub/meshtastic/messages", payload=normalized
        )

    def action(self, action: str) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        paths = {
            "scan": "scan",
            "deliver": "send",
            "retry": "retry",
            "cancel": "cancel",
        }
        if normalized not in paths:
            raise FlexHubClientError("Unsupported FlexHub action")
        return self._endpoint_json("POST", f"/api/flexhub/{paths[normalized]}")

    def mark_meshtastic_read(self) -> dict[str, Any]:
        with self._lock:
            self._meshtastic_console["unread_count"] = 0
            return dict(self._meshtastic_console)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            summary = {
                "configured": bool(self._url),
                "connected": bool(self._status) and not self._error,
                "url": self._url,
                "access_pin_configured": bool(self._access_pin),
                "last_seen": self._last_seen,
                "error": self._error,
                "status": dict(self._status),
                "meshtastic_console": json.loads(
                    json.dumps(self._meshtastic_console, default=str)
                ),
            }
            summary.update(self._route_urls(self._url))
            return summary
