"""FleetConfig validation + mount_controls wiring."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_fleet_core import FleetConfig, mount_controls


def test_bearer_mode_requires_token() -> None:
    with pytest.raises(ValueError, match="requires auth_token"):
        FleetConfig(server_name="x", auth_mode="bearer")


def test_both_mode_requires_token() -> None:
    with pytest.raises(ValueError, match="requires auth_token"):
        FleetConfig(server_name="x", auth_mode="both")


def test_off_mode_no_token_ok() -> None:
    cfg = FleetConfig(server_name="x", auth_mode="off")
    assert cfg.auth_token is None


def test_mount_controls_applies_auth_and_logging() -> None:
    async def ok(_request):
        return JSONResponse({"ok": True})

    async def health(_request):
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[Route("/mcp", ok), Route("/health", health)])
    mount_controls(app, FleetConfig(server_name="x", auth_mode="bearer", auth_token="tok"))
    client = TestClient(app)

    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer tok"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_mount_controls_off_mode_no_auth() -> None:
    async def ok(_request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/mcp", ok)])
    mount_controls(app, FleetConfig(server_name="x", auth_mode="off"))
    assert TestClient(app).get("/mcp").status_code == 200
