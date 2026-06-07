"""Bearer-token auth middleware.

Native equivalent of the Docker MCP Gateway's MCP_GATEWAY_AUTH_TOKEN check —
a single shared secret, constant-time compared, with health routes exempt.
Applied per-server so it works behind Tailscale path fan-out without a gateway.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_BEARER_PREFIX = "Bearer "


class BearerAuthMiddleware:
    """Reject requests lacking a valid ``Authorization: Bearer <token>`` header.

    Constant-time comparison (``secrets.compare_digest``) avoids leaking the
    token via response timing. Exempt path prefixes (health probes) pass through.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        exempt_paths: Iterable[str] = (),
    ) -> None:
        if not token:
            raise ValueError("BearerAuthMiddleware requires a non-empty token")
        self._app = app
        self._token = token
        self._exempt = tuple(exempt_paths)

    def _is_exempt(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in self._exempt)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope)
        if self._is_exempt(request.url.path):
            await self._app(scope, receive, send)
            return

        header = request.headers.get("authorization", "")
        if not header.startswith(_BEARER_PREFIX) or not secrets.compare_digest(
            header[len(_BEARER_PREFIX) :], self._token
        ):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="MCP Fleet"'},
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)
