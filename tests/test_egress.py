"""Egress allowlist policy + httpx client enforcement."""

from __future__ import annotations

import httpx
import pytest

from mcp_fleet_core.egress import EgressError, EgressPolicy, make_async_client


def test_policy_any_port() -> None:
    p = EgressPolicy(["api.ibkr.com"])
    assert p.allows("api.ibkr.com", 443)
    assert p.allows("api.ibkr.com", 80)
    assert not p.allows("evil.com", 443)


def test_policy_port_specific() -> None:
    p = EgressPolicy(["timescaledb:5432"])
    assert p.allows("timescaledb", 5432)
    assert not p.allows("timescaledb", 5433)


def test_policy_case_insensitive_and_blank() -> None:
    p = EgressPolicy(["API.Example.com", "", "  "])
    assert p.allows("api.example.com", 443)
    assert not p.allows("", None)


@pytest.mark.asyncio
async def test_client_blocks_disallowed_host() -> None:
    client = make_async_client(
        EgressPolicy(["allowed.test"]),
        transport=httpx.MockTransport(lambda req: httpx.Response(200)),
    )
    with pytest.raises(EgressError, match="blocked"):
        await client.get("https://blocked.test/x")
    await client.aclose()


@pytest.mark.asyncio
async def test_client_allows_allowlisted_host() -> None:
    client = make_async_client(
        EgressPolicy(["allowed.test"]),
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})),
    )
    resp = await client.get("https://allowed.test/x")
    assert resp.status_code == 200
    await client.aclose()


@pytest.mark.asyncio
async def test_caller_request_hook_preserved() -> None:
    seen: list[str] = []

    async def my_hook(req: httpx.Request) -> None:
        seen.append(str(req.url))

    client = make_async_client(
        EgressPolicy(["allowed.test"]),
        transport=httpx.MockTransport(lambda req: httpx.Response(204)),
        event_hooks={"request": [my_hook]},
    )
    await client.get("https://allowed.test/y")
    assert seen == ["https://allowed.test/y"]
    await client.aclose()
