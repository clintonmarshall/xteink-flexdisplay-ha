"""Local API client for the FlexDisplay bridge."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession


class FlexDisplayApiError(Exception):
    """Raised when the bridge API cannot complete a request."""


class FlexDisplayApiClient:
    """Small asynchronous client for the bridge API."""

    def __init__(self, session: ClientSession, base_url: str, api_key: str = "") -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-FlexDisplay-Bridge-Key": api_key} if api_key else {}

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers,
                json=json,
                timeout=10,
            ) as response:
                if response.status >= 400:
                    try:
                        payload = await response.json()
                        detail = str(payload.get("detail") or response.reason)
                    except (ValueError, AttributeError):
                        detail = response.reason
                    raise FlexDisplayApiError(detail)
                return await response.json()
        except FlexDisplayApiError:
            raise
        except (ClientError, ClientResponseError, TimeoutError, ValueError) as err:
            raise FlexDisplayApiError(str(err)) from err

    async def health(self) -> dict[str, Any]:
        """Return bridge health."""
        return await self._request("GET", "/healthz")

    async def devices(self) -> list[dict[str, Any]]:
        """Return all devices known by the bridge."""
        payload = await self._request("GET", "/api/v1/devices")
        devices = payload.get("devices", [])
        return devices if isinstance(devices, list) else []

    async def command(self, device_id: str, command: str) -> None:
        """Queue a command for a device."""
        await self._request("POST", f"/api/v1/devices/{device_id}/commands/{command}")

    async def cancel_commands(self, device_id: str) -> None:
        """Cancel commands that have not yet reached a device."""
        await self._request("DELETE", f"/api/v1/devices/{device_id}/commands")

    async def provision(self, device_id: str, assignment: dict[str, Any]) -> None:
        """Update the server-side assignment for a device."""
        await self._request("PUT", f"/api/v1/devices/{device_id}/provision", json=assignment)
