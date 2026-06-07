"""Structured per-request call logging.

Native equivalent of the gateway's ``--log-calls``: emits one structured line
per HTTP request to a fleet server, tagged with server lineage, path, status,
and duration. Baseline uses stdlib logging (JSON-shaped extra); an OTLP exporter
can be layered on later without changing call sites.
"""

from __future__ import annotations

import logging
import time

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("mcp_fleet_core.calls")


class CallLoggingMiddleware:
    """Log each HTTP request with server name, method, path, status, duration."""

    def __init__(self, app: ASGIApp, *, server_name: str) -> None:
        self._app = app
        self._server_name = server_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope)
        start = time.perf_counter()
        status_holder = {"code": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "mcp.call",
                extra={
                    "server": self._server_name,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_holder["code"],
                    "duration_ms": duration_ms,
                },
            )
