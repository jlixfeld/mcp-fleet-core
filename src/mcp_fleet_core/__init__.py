"""mcp-fleet-core — shared cross-cutting controls for the FastMCP fleet.

Adoption (one wiring point per server):

    from mcp.server.fastmcp import FastMCP
    from mcp_fleet_core import FleetConfig, build_app

    mcp = FastMCP("strattrader", host="127.0.0.1", port=8040)
    # ... register tools, mount health routes ...

    app = build_app(mcp, FleetConfig(
        server_name="strattrader",
        auth_mode="bearer",
        auth_token=resolved_bearer_token,  # from Infisical
    ))
    # serve `app` with uvicorn instead of mcp.run_streamable_http_async()

Controls shipped in 0.1.0: bearer auth, structured call logging.
Roadmap (tracked issues): egress allowlist, secret-scan response middleware.
"""

from __future__ import annotations

from starlette.applications import Starlette

from .auth import BearerAuthMiddleware
from .config import DEFAULT_EXEMPT_PATHS, AuthMode, FleetConfig
from .logging import CallLoggingMiddleware

__all__ = [
    "FleetConfig",
    "AuthMode",
    "DEFAULT_EXEMPT_PATHS",
    "BearerAuthMiddleware",
    "CallLoggingMiddleware",
    "build_app",
    "mount_controls",
]


def mount_controls(app: Starlette, config: FleetConfig) -> Starlette:
    """Attach the fleet controls to an existing Starlette/ASGI app in place.

    Middleware is applied outermost-first; call logging wraps auth so even
    rejected (401) requests are logged.
    """
    if config.auth_mode in ("bearer", "both"):
        assert config.auth_token is not None  # enforced by FleetConfig validation
        app.add_middleware(
            BearerAuthMiddleware,
            token=config.auth_token,
            exempt_paths=config.exempt_paths,
        )
    if config.log_calls:
        app.add_middleware(CallLoggingMiddleware, server_name=config.server_name)
    return app


def build_app(mcp: object, config: FleetConfig) -> Starlette:
    """Build the hardened ASGI app from a FastMCP instance.

    ``mcp`` is an ``mcp.server.fastmcp.FastMCP``; we call its
    ``streamable_http_app()`` and wrap it with the fleet controls.
    """
    app = mcp.streamable_http_app()  # type: ignore[attr-defined]
    return mount_controls(app, config)
