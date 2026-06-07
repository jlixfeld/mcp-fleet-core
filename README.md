# mcp-fleet-core

Shared cross-cutting controls for the fleet's FastMCP servers. Adopted uniformly
across every MCP server so each one is thinner and identical — the high-value
controls of the Docker MCP Gateway, applied natively (see ADR-33).

**Why a library, not the gateway:** the gateway's security features only fire on
stdio containers it launches per session. The fleet's servers are long-lived and
stateful (IBKR sockets with fixed client IDs, TimescaleDB/Kuzu state, Playwright
sessions) and cannot be launched that way without breaking. Fronting them as
`type: remote` would deliver none of those features while adding a SPOF and
breaking claude.ai-web access. This library delivers the controls to all servers
with no gateway, keeping Infisical + Tailscale + per-server health.

## Install

```toml
# pyproject.toml
dependencies = [
    "mcp-fleet-core @ git+https://github.com/jlixfeld/mcp-fleet-core.git",
]
```

## Use

```python
from mcp.server.fastmcp import FastMCP
from mcp_fleet_core import FleetConfig, build_app

mcp = FastMCP("strattrader", host="127.0.0.1", port=8040)
# register tools + health routes...

app = build_app(mcp, FleetConfig(
    server_name="strattrader",
    auth_mode="bearer",          # bearer | secret_path | both | off
    auth_token=resolved_token,   # resolve from Infisical; library never reads Infisical
))
# serve `app` with uvicorn instead of mcp.run_streamable_http_async()
```

`mount_controls(app, config)` is also exported for servers that already build
their own Starlette app.

## Controls

| Control | Status | Gateway equivalent |
|---|---|---|
| Bearer auth (constant-time, health-exempt) | ✅ 0.1.0 | `MCP_GATEWAY_AUTH_TOKEN` |
| Structured call logging (server/path/status/duration) | ✅ 0.1.0 | `--log-calls` |
| Egress allowlist (httpx client hook) | ✅ 0.2.0 | `allowHosts` / `--block-network` |
| Secret-scan on tool results (redact/block) | ✅ 0.2.0 | `--block-secrets` |
| OTEL spans + metrics (per tool call) | ✅ 0.3.0 (`[otel]` extra) | telemetry |

`build_app` wires the ASGI controls (auth, logging). `harden(mcp, config)` also
installs the tool-layer secret-scan and returns the app. Secret-scan runs at the
MCP tool-result layer (not ASGI) so it never buffers/breaks the streamable-HTTP
stream. Egress is primarily a docker-compose `networks` deny-by-default concern;
`egress_client(config)` adds an in-process httpx hook for defense + audit:

```python
from mcp_fleet_core import FleetConfig, harden, egress_client

cfg = FleetConfig(
    server_name="strattrader", auth_mode="bearer", auth_token=tok,
    allow_hosts=["api.ibkr.com:443", "timescaledb:5432"],
    secret_scan=True, secret_scan_mode="redact", redact_values=[*infisical_values],
)
app = harden(mcp, cfg)                 # auth + logging + secret-scan
client = egress_client(cfg)            # outbound httpx, allowlist-enforced
```

### Telemetry

Set `otlp_endpoint` and install the `[otel]` extra; `harden()` then emits a span
+ `mcp.tool.calls` / `mcp.tool.duration` / `mcp.tool.errors` per tool call,
tagged `mcp.server.name` / `mcp.tool.name`. Unset endpoint or missing extra =
no-op (stdlib call logging stays the baseline; never hard-depends on a running
collector). Push OTLP/gRPC → the fleet OTEL Collector → Prometheus + Grafana
(tailnet-private, ports 8100–8109).

```toml
dependencies = ["mcp-fleet-core[otel] @ git+https://github.com/jlixfeld/mcp-fleet-core.git"]
```

## Auth modes

- `bearer` — require `Authorization: Bearer <token>`. iOS/SDK clients.
- `secret_path` — trust the Tailscale secret-path (claude.ai **web** cannot send
  custom headers). Web-facing servers.
- `both` — accept either. Servers serving web **and** programmatic clients
  (e.g. HealthBridge).
- `off` — no auth (stdio/local only).

## Dev

```bash
uv sync
uv run pytest
uv run ruff check .
```
