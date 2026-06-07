"""Call logging middleware emits structured lineage."""

from __future__ import annotations

import logging

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_fleet_core.logging import CallLoggingMiddleware


def _app() -> Starlette:
    async def ok(_request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/mcp", ok)])
    app.add_middleware(CallLoggingMiddleware, server_name="strattrader")
    return app


def test_logs_server_path_status_duration(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(_app())
    with caplog.at_level(logging.INFO, logger="mcp_fleet_core.calls"):
        resp = client.get("/mcp")
    assert resp.status_code == 200

    records = [r for r in caplog.records if r.message == "mcp.call"]
    assert len(records) == 1
    rec = records[0]
    # Assert the CONTENT of the structured log, not merely that it fired.
    assert rec.server == "strattrader"
    assert rec.method == "GET"
    assert rec.path == "/mcp"
    assert rec.status == 200
    assert isinstance(rec.duration_ms, float)
    assert rec.duration_ms >= 0
