"""Secret-scan on tool results.

Native equivalent of the gateway's ``--block-secrets``. Operates at the MCP
tool-result layer (wrapping each tool's function), NOT as ASGI body middleware —
the fleet uses streamable-HTTP (SSE-style), so buffering response bytes would
break streaming. Wrapping the tool fn scans the Python result *before*
serialization, which is also where the gateway inspects payloads.

Two detection modes, combined:
- exact-value: redact known secret VALUES the server resolved from Infisical
  (passed in by the caller; this library never reads Infisical itself).
- pattern: a conservative set of secret-shaped regexes. Kept minimal to avoid
  false positives on legitimate trading/health payloads (ADR-33 Q3).
"""

from __future__ import annotations

import functools
import inspect
import logging
import re
from collections.abc import Iterable
from typing import Literal

logger = logging.getLogger("mcp_fleet_core.secretscan")

REDACTED = "[REDACTED]"
ScanMode = Literal["redact", "block"]

# Conservative, high-signal patterns only. Each must be unlikely to match
# ordinary numeric/financial/health payloads.
DEFAULT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),          # OpenAI-style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),   # GitHub tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),             # AWS access key id
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), # Slack
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\."          # JWT (header.payload.sig)
               r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)


class SecretLeakError(RuntimeError):
    """Raised in ``block`` mode when a tool result contains secret material."""


def scrub(
    text: str,
    secret_values: Iterable[str],
    patterns: Iterable[re.Pattern[str]] = DEFAULT_PATTERNS,
) -> tuple[str, int]:
    """Return ``(scrubbed_text, hit_count)``. Exact values first, then patterns."""
    hits = 0
    for value in secret_values:
        if value and value in text:
            hits += text.count(value)
            text = text.replace(value, REDACTED)
    for pattern in patterns:
        text, n = pattern.subn(REDACTED, text)
        hits += n
    return text, hits


def _walk(obj: object, secret_values, patterns) -> tuple[object, int]:
    """Recursively scrub strings within str/list/tuple/dict; leave others as-is."""
    if isinstance(obj, str):
        return scrub(obj, secret_values, patterns)
    if isinstance(obj, list):
        out, total = [], 0
        for item in obj:
            scrubbed, n = _walk(item, secret_values, patterns)
            out.append(scrubbed)
            total += n
        return out, total
    if isinstance(obj, tuple):
        items = [_walk(i, secret_values, patterns) for i in obj]
        return tuple(i for i, _ in items), sum(n for _, n in items)
    if isinstance(obj, dict):
        out, total = {}, 0
        for key, val in obj.items():
            scrubbed, n = _walk(val, secret_values, patterns)
            out[key] = scrubbed
            total += n
        return out, total
    return obj, 0


def install_secret_scan(
    mcp: object,
    *,
    secret_values: Iterable[str] = (),
    mode: ScanMode = "redact",
    extra_patterns: Iterable[re.Pattern[str]] = (),
    server_name: str = "",
) -> None:
    """Wrap every registered tool so its result is secret-scanned.

    ``redact`` replaces secret material with ``[REDACTED]`` and forwards the
    result; ``block`` raises ``SecretLeakError`` instead. Hit COUNTS are logged
    with server lineage — never the secret value itself.

    Relies on the official SDK's tool registry (``mcp._tool_manager._tools``).
    """
    secret_values = tuple(v for v in secret_values if v)
    patterns = (*DEFAULT_PATTERNS, *extra_patterns)
    tools = mcp._tool_manager._tools  # type: ignore[attr-defined]

    def _handle(result: object, tool_name: str) -> object:
        scrubbed, hits = _walk(result, secret_values, patterns)
        if hits:
            logger.warning(
                "mcp.secret_scan.hit",
                extra={"server": server_name, "tool": tool_name, "hits": hits, "mode": mode},
            )
            if mode == "block":
                raise SecretLeakError(
                    f"tool {tool_name!r} result contained {hits} secret(s); blocked"
                )
        return scrubbed

    for name, tool in tools.items():
        original = tool.fn

        if inspect.iscoroutinefunction(original):

            @functools.wraps(original)
            async def awrapped(*args, __fn=original, __name=name, **kwargs):
                return _handle(await __fn(*args, **kwargs), __name)

            tool.fn = awrapped
        else:

            @functools.wraps(original)
            def wrapped(*args, __fn=original, __name=name, **kwargs):
                return _handle(__fn(*args, **kwargs), __name)

            tool.fn = wrapped
