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
from .egress import EgressError, EgressPolicy, make_async_client
from .logging import CallLoggingMiddleware
from .secretscan import SecretLeakError, install_secret_scan, scrub

__all__ = [
    "FleetConfig",
    "AuthMode",
    "DEFAULT_EXEMPT_PATHS",
    "BearerAuthMiddleware",
    "CallLoggingMiddleware",
    "EgressPolicy",
    "EgressError",
    "make_async_client",
    "egress_client",
    "SecretLeakError",
    "install_secret_scan",
    "scrub",
    "build_app",
    "mount_controls",
    "harden",
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
    ``streamable_http_app()`` and wrap it with the ASGI controls (auth, logging).
    Tool-layer controls (secret-scan) are applied by :func:`harden`.
    """
    app = mcp.streamable_http_app()  # type: ignore[attr-defined]
    return mount_controls(app, config)


def egress_client(config: FleetConfig, **kwargs: object):
    """Build an egress-checked ``httpx.AsyncClient`` from ``config.allow_hosts``."""
    return make_async_client(EgressPolicy(config.allow_hosts), **kwargs)


def harden(mcp: object, config: FleetConfig) -> Starlette:
    """One-call hardening: install tool-layer secret-scan, then build the ASGI app.

    Egress is call-site scoped — use :func:`egress_client` where the server
    builds its outbound httpx clients.
    """
    if config.secret_scan:
        install_secret_scan(
            mcp,
            secret_values=config.redact_values,
            mode=config.secret_scan_mode,
            server_name=config.server_name,
        )
    return build_app(mcp, config)
