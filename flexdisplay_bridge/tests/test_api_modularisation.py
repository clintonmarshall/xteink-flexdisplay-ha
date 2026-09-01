from __future__ import annotations

from pathlib import Path

from fastapi.routing import APIRoute

from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import BridgeConfig


EXPECTED_FLEXHUB_ROUTES = {
    ("GET", "/api/v1/flexhub"),
    ("PUT", "/api/v1/flexhub/settings"),
    ("POST", "/api/v1/flexhub/refresh"),
    ("GET", "/api/v1/flexhub/meshtastic/messages"),
    ("POST", "/api/v1/flexhub/meshtastic/messages"),
    ("GET", "/api/v1/flexhub/meshtastic/nodes"),
    ("GET", "/api/v1/flexhub/meshtastic/settings"),
    ("PUT", "/api/v1/flexhub/meshtastic/settings"),
    ("POST", "/api/v1/flexhub/meshtastic/read"),
    ("POST", "/api/v1/flexhub/actions/{action}"),
}


def _api_routes(routes: list[object]):
    """Walk FastAPI's nested included-router representation."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        nested = getattr(route, "routes", None)
        if isinstance(nested, list):
            yield from _api_routes(nested)
        included = getattr(route, "original_router", None)
        included_routes = getattr(included, "routes", None)
        if isinstance(included_routes, list):
            yield from _api_routes(included_routes)


def test_flexhub_routes_are_owned_by_the_concern_router(tmp_path: Path) -> None:
    app = create_app(BridgeConfig(state_path=tmp_path / "state.json"))
    observed: set[tuple[str, str]] = set()

    for route in _api_routes(app.routes):
        if not route.path.startswith("/api/v1/flexhub"):
            continue
        assert route.endpoint.__module__ == "flexdisplay_bridge.api.flexhub"
        observed.update(
            (method, route.path)
            for method in route.methods
            if method in {"GET", "POST", "PUT", "DELETE", "PATCH"}
        )

    assert observed == EXPECTED_FLEXHUB_ROUTES


def test_flexhub_operation_ids_remain_unique_after_router_composition(
    tmp_path: Path,
) -> None:
    app = create_app(BridgeConfig(state_path=tmp_path / "state.json"))
    operation_ids = [
        operation["operationId"]
        for path, methods in app.openapi()["paths"].items()
        if path.startswith("/api/v1/flexhub")
        for operation in methods.values()
    ]

    assert len(operation_ids) == len(EXPECTED_FLEXHUB_ROUTES)
    assert len(operation_ids) == len(set(operation_ids))
