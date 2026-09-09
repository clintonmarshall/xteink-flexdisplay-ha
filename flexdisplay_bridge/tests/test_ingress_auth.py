from dataclasses import replace
import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from flexdisplay_bridge.app import create_app
from flexdisplay_bridge.config import BridgeConfig, load_config
from flexdisplay_bridge.ingress_auth import authenticated_ingress


HEADERS = {
    "x-ingress-path": "/api/hassio_ingress/example-session_123",
    "x-remote-user-id": "a" * 32,
}


@pytest.mark.parametrize("peer,enabled,headers,expected", [
    ("172.30.32.2", True, HEADERS, True),
    ("172.30.32.2", False, HEADERS, False),
    ("127.0.0.1", True, HEADERS, False),
    ("172.30.32.3", True, HEADERS, False),
    ("10.200.40.4", True, {**HEADERS, "x-forwarded-for": "172.30.32.2"}, False),
    ("172.30.32.2", True, {}, False),
    ("172.30.32.2", True, {"x-ingress-path": HEADERS["x-ingress-path"]}, False),
    ("172.30.32.2", True, {**HEADERS, "x-ingress-path": "/studio/"}, False),
    ("172.30.32.2", True, {**HEADERS, "x-remote-user-id": ""}, False),
])
def test_ingress_boundary(peer, enabled, headers, expected):
    request = Request({"type": "http", "client": (peer, 1234),
                       "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})
    assert authenticated_ingress(request, enabled=enabled) is expected


def test_duplicate_headers_and_missing_peer_fail_closed():
    headers = [(k.encode(), v.encode()) for k, v in HEADERS.items()]
    for client, extra in [(None, []), (("172.30.32.2", 1234), [headers[0]])]:
        request = Request({"type": "http", "client": client, "headers": headers + extra})
        assert not authenticated_ingress(request, enabled=True)


def test_management_auth_keeps_direct_key_fallback(tmp_path):
    config = BridgeConfig(state_path=tmp_path / "state.json", api_key="test-secret",
                          ingress_auth_enabled=True)
    app = create_app(config)
    with TestClient(app, client=("172.30.32.2", 1234)) as client:
        assert client.get("/api/v1/devices", headers=HEADERS).status_code == 200
        assert client.get("/api/v1/devices").status_code == 401
    with TestClient(app, client=("10.200.40.50", 1234)) as client:
        assert client.get("/api/v1/devices", headers=HEADERS).status_code == 401
        assert client.get("/api/v1/devices", headers={"X-FlexDisplay-Bridge-Key": "test-secret"}).status_code == 200
    with TestClient(create_app(replace(config, ingress_auth_enabled=False)),
                    client=("172.30.32.2", 1234)) as client:
        assert client.get("/api/v1/devices", headers=HEADERS).status_code == 401


def test_ingress_opt_in_is_exact(monkeypatch, tmp_path):
    path = tmp_path / "missing.yaml"
    monkeypatch.delenv("FLEXDISPLAY_INGRESS_AUTH_ENABLED", raising=False)
    assert not load_config(path).ingress_auth_enabled
    monkeypatch.setenv("FLEXDISPLAY_INGRESS_AUTH_ENABLED", "true")
    assert load_config(path).ingress_auth_enabled
    monkeypatch.setenv("FLEXDISPLAY_INGRESS_AUTH_ENABLED", "1")
    assert not load_config(path).ingress_auth_enabled


def test_app_runner_never_trusts_forwarded_peer():
    tree = ast.parse((Path(__file__).parents[1] / "app_runner.py").read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name)
             and node.func.value.id == "uvicorn" and node.func.attr == "run"]
    assert len(calls) == 1
    values = {item.arg: item.value for item in calls[0].keywords}
    assert isinstance(values["proxy_headers"], ast.Constant)
    assert values["proxy_headers"].value is False
