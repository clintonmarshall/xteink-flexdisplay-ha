from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class FlexHubClientError(ValueError):
    pass


class FlexHubClient:
    """Persist a FlexHub connection and expose its bounded status endpoint."""

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
        self._url = ""
        self._access_pin = ""
        self._status: dict[str, Any] = {}
        self._last_seen = ""
        self._error = ""
        self._load(default_url, default_access_pin)

    @staticmethod
    def _url_value(value: str) -> str:
        candidate = str(value or "").strip().rstrip("/")
        if not candidate:
            return ""
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise FlexHubClientError("FlexHub URL must start with http:// or https://")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise FlexHubClientError("FlexHub URL cannot contain credentials, a query, or a fragment")
        if len(candidate) > 240:
            raise FlexHubClientError("FlexHub URL is too long")
        return candidate

    def _load(self, default_url: str, default_access_pin: str) -> None:
        payload: dict[str, Any] = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (FileNotFoundError, OSError, ValueError):
            pass
        self._url = self._url_value(str(payload.get("url") or default_url or ""))
        self._access_pin = str(payload.get("access_pin") or default_access_pin or "")[:64]

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
        try:
            response = requests.get(
                f"{url}/api/flexhub/status",
                headers=headers,
                timeout=self.timeout_seconds,
                allow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise FlexHubClientError("FlexHub returned an invalid status response")
        except (requests.RequestException, ValueError) as exc:
            with self._lock:
                self._error = str(exc)[:240]
            return self.summary()

        with self._lock:
            self._status = payload
            self._last_seen = datetime.now(UTC).isoformat(timespec="seconds")
            self._error = ""
        return self.summary()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "configured": bool(self._url),
                "connected": bool(self._status) and not self._error,
                "url": self._url,
                "access_pin_configured": bool(self._access_pin),
                "last_seen": self._last_seen,
                "error": self._error,
                "status": dict(self._status),
            }
