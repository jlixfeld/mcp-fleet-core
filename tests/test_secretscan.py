"""Secret-scan: scrub fn, structure walking, and tool-fn installation."""

from __future__ import annotations

import logging

import pytest

from mcp_fleet_core.secretscan import (
    REDACTED,
    SecretLeakError,
    _walk,
    install_secret_scan,
    scrub,
)

SECRET = "super-secret-token-value-123"


def test_scrub_exact_value() -> None:
    text, hits = scrub(f"key is {SECRET} ok", [SECRET])
    assert text == f"key is {REDACTED} ok"
    assert hits == 1


def test_scrub_counts_multiple_occurrences() -> None:
    _, hits = scrub(f"{SECRET} and {SECRET}", [SECRET])
    assert hits == 2


def test_scrub_pattern_github_token() -> None:
    token = "ghp_" + "a" * 36
    text, hits = scrub(f"token={token}", [])
    assert REDACTED in text and token not in text
    assert hits == 1


def test_scrub_no_false_positive_on_financial_payload() -> None:
    payload = "price 123.45 qty 1000 pnl -42.7 clientId 12 bars 60"
    text, hits = scrub(payload, [])
    assert text == payload
    assert hits == 0


def test_walk_nested_structure() -> None:
    obj = {"a": [f"x {SECRET}", {"b": SECRET}], "n": 5}
    scrubbed, hits = _walk(obj, [SECRET], ())
    assert scrubbed == {"a": [f"x {REDACTED}", {"b": REDACTED}], "n": 5}
    assert hits == 2


class _Tool:
    def __init__(self, fn):
        self.fn = fn


class _Manager:
    def __init__(self, tools):
        self._tools = tools


class _FakeMCP:
    """Mimics the SDK surface install_secret_scan relies on."""

    def __init__(self, tools):
        self._tool_manager = _Manager(tools)


@pytest.mark.asyncio
async def test_install_redacts_async_tool_result(caplog: pytest.LogCaptureFixture) -> None:
    async def leaky():
        return {"data": f"here: {SECRET}"}

    mcp = _FakeMCP({"leaky": _Tool(leaky)})
    with caplog.at_level(logging.WARNING, logger="mcp_fleet_core.secretscan"):
        install_secret_scan(mcp, secret_values=[SECRET], server_name="srv")
        result = await mcp._tool_manager._tools["leaky"].fn()

    assert result == {"data": f"here: {REDACTED}"}
    rec = next(r for r in caplog.records if r.message == "mcp.secret_scan.hit")
    # Hit COUNT logged with lineage; the secret value itself must NOT appear.
    assert rec.server == "srv"
    assert rec.tool == "leaky"
    assert rec.hits == 1
    assert SECRET not in caplog.text


def test_install_sync_tool_result() -> None:
    def leaky():
        return f"x {SECRET}"

    mcp = _FakeMCP({"leaky": _Tool(leaky)})
    install_secret_scan(mcp, secret_values=[SECRET])
    assert mcp._tool_manager._tools["leaky"].fn() == f"x {REDACTED}"


def test_block_mode_raises_on_hit() -> None:
    def leaky():
        return SECRET

    mcp = _FakeMCP({"leaky": _Tool(leaky)})
    install_secret_scan(mcp, secret_values=[SECRET], mode="block")
    with pytest.raises(SecretLeakError, match="blocked"):
        mcp._tool_manager._tools["leaky"].fn()


def test_clean_result_passes_through() -> None:
    def clean():
        return {"price": 100, "ok": True}

    mcp = _FakeMCP({"clean": _Tool(clean)})
    install_secret_scan(mcp, secret_values=[SECRET], mode="block")
    assert mcp._tool_manager._tools["clean"].fn() == {"price": 100, "ok": True}
