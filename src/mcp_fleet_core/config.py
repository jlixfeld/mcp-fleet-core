"""Fleet control configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AuthMode = Literal["bearer", "secret_path", "both", "off"]

# Health routes are never authenticated — liveness/readiness probes must work
# without credentials (Docker healthcheck, K8s-style probes, tailnet smoke tests).
DEFAULT_EXEMPT_PATHS: tuple[str, ...] = ("/health", "/health/live", "/health/ready")


class FleetConfig(BaseModel):
    """Per-server configuration for the shared fleet controls.

    Secret VALUES (the bearer token) are passed in already-resolved from
    Infisical by the calling server — this library never reads Infisical itself.
    """

    server_name: str = Field(..., description="Stable server identity, used in log lineage.")
    auth_mode: AuthMode = Field(
        "bearer",
        description=(
            "bearer: require Authorization: Bearer <token>. "
            "secret_path: trust the tailscale secret-path (claude.ai web cannot send headers). "
            "both: accept either. off: no auth (only for stdio/local)."
        ),
    )
    auth_token: str | None = Field(
        None,
        description="Resolved bearer token (from Infisical). Required for auth_mode bearer/both.",
    )
    exempt_paths: tuple[str, ...] = Field(
        DEFAULT_EXEMPT_PATHS,
        description="Path prefixes that bypass auth (health probes).",
    )
    log_calls: bool = Field(True, description="Emit a structured log line per tool call.")

    def model_post_init(self, _context: object) -> None:
        if self.auth_mode in ("bearer", "both") and not self.auth_token:
            raise ValueError(
                f"auth_mode={self.auth_mode!r} requires auth_token "
                f"(resolve it from Infisical before constructing FleetConfig)"
            )
