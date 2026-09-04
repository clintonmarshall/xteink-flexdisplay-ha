"""Local API client for the FlexDisplay bridge."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from aiohttp import ClientError, ClientResponseError, ClientSession


class FlexDisplayApiError(Exception):
    """Raised when the bridge API cannot complete a request."""


class FlexDisplayApiClient:
    """Small asynchronous client for the bridge API."""

    def __init__(
        self, session: ClientSession, base_url: str, api_key: str = ""
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-FlexDisplay-Bridge-Key": api_key} if api_key else {}

    async def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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

    async def _request_bytes(self, path: str) -> bytes:
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                headers=self._headers,
                timeout=10,
            ) as response:
                if response.status >= 400:
                    raise FlexDisplayApiError(response.reason)
                return await response.read()
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

    async def current_screen(self, device_id: str) -> bytes:
        """Return the most recent rendered screen as PNG."""
        return await self._request_bytes(
            f"/api/v1/devices/{device_id}/screens/current.png"
        )

    async def camera_snapshot(self, device_id: str) -> bytes:
        """Return the latest explicitly captured Android camera JPEG."""
        return await self._request_bytes(
            f"/api/v1/devices/{device_id}/camera/snapshot"
        )

    async def request_camera_snapshot(self, device_id: str) -> None:
        """Queue one user-initiated camera snapshot request."""
        await self._request(
            "POST", f"/api/v1/devices/{device_id}/camera/snapshot/request"
        )

    async def command(self, device_id: str, command: str) -> None:
        """Queue a command for a device."""
        await self._request("POST", f"/api/v1/devices/{device_id}/commands/{command}")

    async def cancel_commands(self, device_id: str) -> None:
        """Cancel queued commands and stop retries for delivered durable commands."""
        await self._request("DELETE", f"/api/v1/devices/{device_id}/commands")

    async def retry_firmware(self, device_id: str) -> None:
        """Retry a failed firmware installation under the Bridge safety gates."""
        await self._request("POST", f"/api/v1/devices/{device_id}/firmware/retry")

    async def reset_firmware_rollout(self) -> None:
        """Reset the configured release rollout and cancel active installs."""
        await self._request("POST", "/api/v1/firmware/rollout/reset")

    async def refresh_firmware_mirror(self) -> None:
        """Refresh and verify the Bridge's local firmware mirror."""
        await self._request("POST", "/api/v1/firmware/mirror/refresh")

    async def verify_usb_recovery(
        self,
        device_id: str,
        expected_target_version: str,
        expected_command_id: str,
    ) -> None:
        """Reconcile a USB-recovered canary using the Bridge's guarded workflow."""
        await self._request(
            "POST",
            f"/api/v1/devices/{device_id}/firmware/verify-usb-recovery",
            json={
                "expected_target_version": expected_target_version,
                "expected_command_id": expected_command_id,
            },
        )

    async def provision(self, device_id: str, assignment: dict[str, Any]) -> None:
        """Update the server-side assignment for a device."""
        await self._request(
            "PUT", f"/api/v1/devices/{device_id}/provision", json=assignment
        )

    async def voice_settings(self, device_id: str, settings: dict[str, Any]) -> None:
        """Update receiver speaker controls."""
        await self._request(
            "PUT", f"/api/v1/devices/{device_id}/voice", json=settings
        )

    async def display_settings(self, device_id: str, settings: dict[str, Any]) -> None:
        """Update receiver display controls."""
        await self._request(
            "PUT", f"/api/v1/devices/{device_id}/display", json=settings
        )

    async def notify(
        self,
        device_id: str,
        *,
        title: str,
        message: str = "",
        camera_entity: str = "",
        chime: str = "default",
        duration: int = 20,
        actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Push an interactive notification to an Android FlexDisplay receiver."""
        return await self._request(
            "POST",
            f"/api/v1/devices/{device_id}/notifications",
            json={
                "title": title,
                "message": message,
                "camera_entity": camera_entity,
                "chime": chime,
                "duration": duration,
                "actions": actions or [],
            },
        )

    async def clear_notification(self, device_id: str) -> dict[str, Any]:
        """Clear the active Android receiver alert."""
        return await self._request(
            "DELETE", f"/api/v1/devices/{device_id}/notifications/current"
        )

    async def apply_policy(
        self,
        profile: str,
        *,
        scope: str = "all",
        device_ids: list[str] | None = None,
        delivery: str = "when_awake",
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply one named policy to a fleet scope."""
        return await self._request(
            "PUT",
            "/api/v1/fleet/policy",
            json={
                "profile": profile,
                "scope": scope,
                "device_ids": device_ids or [],
                "delivery": delivery,
                "overrides": overrides or {},
            },
        )

    async def button_actions(self, device_id: str) -> dict[str, Any]:
        """Return physical-button action mappings for one device."""
        return await self._request("GET", f"/api/v1/devices/{device_id}/button-actions")

    async def set_button_actions(
        self,
        device_id: str,
        mappings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Replace physical-button action mappings for one device."""
        return await self._request(
            "PUT",
            f"/api/v1/devices/{device_id}/button-actions",
            json={"mappings": mappings},
        )

    async def flexhub(self) -> dict[str, Any]:
        """Return the configured FlexHub and Meshtastic status."""
        return await self._request("GET", "/api/v1/flexhub")

    async def flexhub_meshtastic_messages(
        self,
        *,
        after: int = 0,
        limit: int = 30,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Return recent messages from the FlexHub's bounded console."""
        values: dict[str, Any] = {
            "after": max(0, after),
            "limit": max(1, min(limit, 32)),
        }
        if session_id:
            values["session_id"] = session_id
        query = urlencode(values)
        return await self._request(
            "GET",
            f"/api/v1/flexhub/meshtastic/messages?{query}",
        )

    async def send_meshtastic_message(
        self,
        *,
        text: str,
        destination: str = "broadcast",
        channel: int = 0,
        request_ack: bool = False,
    ) -> dict[str, Any]:
        """Send a broadcast or direct message through the FlexHub."""
        return await self._request(
            "POST",
            "/api/v1/flexhub/meshtastic/messages",
            json={
                "text": text,
                "destination": destination,
                "channel": channel,
                "request_ack": request_ack,
            },
        )

    async def flexhub_action(self, action: str) -> dict[str, Any]:
        """Run one bounded receiver-fleet action on the FlexHub."""
        return await self._request("POST", f"/api/v1/flexhub/actions/{action}")

    async def mark_meshtastic_read(self) -> dict[str, Any]:
        """Reset the Bridge-side Meshtastic unread counter."""
        return await self._request("POST", "/api/v1/flexhub/meshtastic/read")

    async def pending_top52810_job(self, address: str) -> dict[str, Any] | None:
        """Return the newest hash-confirmed job waiting for this BLE address."""
        payload = await self._request(
            "GET", f"/api/v1/stock-ble/top52810/jobs/pending/{address}"
        )
        job = payload.get("job")
        return job if isinstance(job, dict) else None

    async def claim_top52810_job(
        self, job_id: str, executor_id: str
    ) -> dict[str, Any]:
        """Atomically bind a pending stock-BLE job to this HA executor."""
        return await self._request(
            "POST",
            f"/api/v1/stock-ble/top52810/jobs/{job_id}/claim",
            json={"executor_id": executor_id},
        )

    async def report_top52810_job(
        self,
        job_id: str,
        *,
        lease: str,
        status: str,
        detail: str = "",
    ) -> dict[str, Any]:
        """Record the terminal transport result without claiming physical success."""
        return await self._request(
            "POST",
            f"/api/v1/stock-ble/top52810/jobs/{job_id}/report",
            json={"lease": lease, "status": status, "detail": detail},
        )
