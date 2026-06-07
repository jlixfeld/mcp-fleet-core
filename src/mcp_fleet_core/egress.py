"""Outbound egress allowlist.

Native equivalent of the Docker MCP Gateway's ``allowHosts`` / ``--block-network``.

The real boundary is a docker-compose deny-by-default network; this in-process
hook is defense-in-depth + audit: an httpx client that refuses to connect to any
host not on the allowlist, so a compromised or buggy tool cannot phone home to an
unexpected destination even if the network layer is misconfigured.
"""

from __future__ import annotations

from collections.abc import Iterable

import httpx


class EgressError(RuntimeError):
    """Raised when a request targets a host outside the allowlist."""


class EgressPolicy:
    """Allowlist of outbound destinations.

    Entries are ``host`` (any port) or ``host:port`` (that port only).
    Matching is exact on host; no wildcards (keep the policy auditable).
    """

    def __init__(self, allow_hosts: Iterable[str]) -> None:
        # host -> set of allowed ports; empty set means "any port".
        self._rules: dict[str, set[int]] = {}
        for entry in allow_hosts:
            entry = entry.strip()
            if not entry:
                continue
            host, _, port = entry.partition(":")
            host = host.lower()
            ports = self._rules.setdefault(host, set())
            if port:
                ports.add(int(port))

    def allows(self, host: str, port: int | None) -> bool:
        ports = self._rules.get((host or "").lower())
        if ports is None:
            return False
        return not ports or port is None or port in ports

    async def _hook(self, request: httpx.Request) -> None:
        url = request.url
        if not self.allows(url.host, url.port):
            raise EgressError(
                f"egress to {url.host}:{url.port or ''} blocked "
                f"(not in allowlist {sorted(self._rules)})"
            )


def make_async_client(policy: EgressPolicy, **kwargs: object) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` whose requests are checked against ``policy``.

    Any extra kwargs are forwarded to ``httpx.AsyncClient``. A caller-supplied
    request event hook is preserved and runs after the egress check.
    """
    hooks = dict(kwargs.pop("event_hooks", {}) or {})  # type: ignore[arg-type]
    request_hooks = [policy._hook, *hooks.get("request", [])]
    hooks["request"] = request_hooks
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)  # type: ignore[arg-type]
