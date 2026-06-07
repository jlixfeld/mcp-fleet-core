"""Bearer auth middleware behavior."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_fleet_core.auth import BearerAuthMiddleware

TOKEN = "s3cr3t-fleet-token"


def _app() -> Starlette:
    async def protected(_request):
        return JSONResponse({"ok": True})

    async def health(_request):
        return JSONResponse({"status": "ok"})

    app = Starlette(
        routes=[
            Route("/mcp", protected),
            Route("/health", health),
            Route("/health/ready", health),
        ]
    )
    app.add_middleware(
        BearerAuthMiddleware,
        token=TOKEN,
        exempt_paths=("/health", "/health/live", "/health/ready"),
    )
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


def test_valid_token_passes(client: TestClient) -> None:
    resp = client.get("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_missing_header_rejected(client: TestClient) -> None:
    resp = client.get("/mcp")
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}
    assert resp.headers["WWW-Authenticate"] == 'Bearer realm="MCP Fleet"'


def test_wrong_token_rejected(client: TestClient) -> None:
    resp = client.get("/mcp", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_non_bearer_scheme_rejected(client: TestClient) -> None:
    resp = client.get("/mcp", headers={"Authorization": f"Basic {TOKEN}"})
    assert resp.status_code == 401


def test_health_exempt_without_token(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_empty_token_construction_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty token"):
        BearerAuthMiddleware(lambda *a: None, token="")
