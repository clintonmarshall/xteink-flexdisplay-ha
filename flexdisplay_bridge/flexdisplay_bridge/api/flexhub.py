"""FlexHub and Meshtastic HTTP routes.

The Bridge composition root supplies the concrete services and callbacks.  Keeping
those dependencies explicit lets this router remain independently testable while
the existing application state aliases stay compatible with operators and tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from ..flexhub_client import FlexHubClient, FlexHubClientError
from ..meshtastic_console import (
    MeshtasticConsoleStore,
    MeshtasticConsoleValidationError,
)
from ..mqtt_service import MqttService


AuthorizeRequest = Callable[[Request], None]
ProcessMeshtasticMessages = Callable[
    [list[dict[str, Any]]],
    list[dict[str, Any]],
]


@dataclass(frozen=True)
class FlexHubRouterDependencies:
    """Runtime dependencies owned by the Bridge composition root."""

    flexhub: FlexHubClient
    mqtt: MqttService
    meshtastic_console: MeshtasticConsoleStore
    authorize: AuthorizeRequest
    process_messages: ProcessMeshtasticMessages


def _proxy_status(error: FlexHubClientError) -> int:
    return error.status_code if error.status_code in {409, 413, 429, 503} else 502


def create_flexhub_router(dependencies: FlexHubRouterDependencies) -> APIRouter:
    """Build the complete FlexHub API surface with explicit dependencies."""
    router = APIRouter(prefix="/api/v1/flexhub", tags=["flexhub"])
    flexhub = dependencies.flexhub
    mqtt = dependencies.mqtt
    meshtastic_console = dependencies.meshtastic_console
    authorize = dependencies.authorize
    process_messages = dependencies.process_messages

    @router.get("")
    def flexhub_status(request: Request, refresh: bool = False) -> dict[str, Any]:
        authorize(request)
        summary = (
            flexhub.poll() if refresh and flexhub.configured else flexhub.summary()
        )
        mqtt.publish_flexhub(summary)
        return summary

    @router.put("/settings")
    def configure_flexhub(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        authorize(request)
        try:
            flexhub.configure(
                str(payload.get("url") or ""),
                str(payload.get("access_pin") or ""),
            )
        except FlexHubClientError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        summary = flexhub.poll() if flexhub.configured else flexhub.summary()
        mqtt.publish_flexhub(summary)
        return summary

    @router.post("/refresh")
    def refresh_flexhub(request: Request) -> dict[str, Any]:
        authorize(request)
        summary = flexhub.poll()
        mqtt.publish_flexhub(summary)
        return summary

    @router.get("/meshtastic/messages")
    def flexhub_meshtastic_messages(
        request: Request,
        after: int = 0,
        limit: int = 30,
        session_id: int | None = None,
        query: str = "",
        direction: str = "",
        channel: int | None = None,
        node: str = "",
    ) -> dict[str, Any]:
        authorize(request)
        try:
            result, observed = flexhub.fetch_messages(
                after=after,
                limit=limit,
                session_id=session_id,
                query=query,
                direction=direction,
                channel=channel,
                node=node,
            )
        except FlexHubClientError as err:
            status = 400 if str(err).startswith("Meshtastic") else _proxy_status(err)
            raise HTTPException(status_code=status, detail=str(err)) from err
        processed = process_messages(observed)
        return {
            **result,
            "bridge": {
                "new_messages": len(processed),
                "console": flexhub.summary()["meshtastic_console"],
            },
        }

    @router.get("/meshtastic/nodes")
    def flexhub_meshtastic_nodes(request: Request) -> dict[str, Any]:
        authorize(request)
        try:
            return flexhub.meshtastic_nodes()
        except FlexHubClientError as err:
            raise HTTPException(status_code=_proxy_status(err), detail=str(err)) from err

    @router.post("/meshtastic/messages")
    def send_flexhub_meshtastic_message(
        payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        authorize(request)
        try:
            normalized = FlexHubClient.normalize_meshtastic_message(payload)
        except FlexHubClientError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        try:
            return flexhub.send_meshtastic_message(normalized)
        except FlexHubClientError as err:
            raise HTTPException(status_code=_proxy_status(err), detail=str(err)) from err

    @router.post("/actions/{action}")
    def run_flexhub_action(action: str, request: Request) -> dict[str, Any]:
        authorize(request)
        try:
            return flexhub.action(action)
        except FlexHubClientError as err:
            status = 400 if str(err) == "Unsupported FlexHub action" else _proxy_status(err)
            raise HTTPException(status_code=status, detail=str(err)) from err

    @router.get("/meshtastic/settings")
    def flexhub_meshtastic_settings(request: Request) -> dict[str, Any]:
        authorize(request)
        return meshtastic_console.payload()

    @router.put("/meshtastic/settings")
    def save_flexhub_meshtastic_settings(
        payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        authorize(request)
        try:
            return meshtastic_console.replace(payload)
        except MeshtasticConsoleValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

    @router.post("/meshtastic/read")
    def mark_flexhub_meshtastic_read(request: Request) -> dict[str, Any]:
        authorize(request)
        return {"meshtastic_console": flexhub.mark_meshtastic_read()}

    return router
