"""OpenTelemetry instrumentation at the MCP tool-call layer.

Native equivalent of the gateway's telemetry: per-tool-call span + metrics
(``mcp.tool.calls`` / ``mcp.tool.duration`` / ``mcp.tool.errors``), each tagged
with server + tool lineage. Instrumented at the tool-fn layer (like secret-scan)
because per-tool attribution is invisible at the ASGI/HTTP layer for
streamable-HTTP.

OTEL is an OPTIONAL extra (``mcp-fleet-core[otel]``). Without it installed, or
without an ``otlp_endpoint``, telemetry is a no-op and the stdlib call logging
remains the audit baseline — the library never hard-depends on a running
collector.
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import FleetConfig

CALLS = "mcp.tool.calls"
DURATION = "mcp.tool.duration"
ERRORS = "mcp.tool.errors"


def _otel_available() -> bool:
    try:
        import opentelemetry.metrics  # noqa: F401
        import opentelemetry.trace  # noqa: F401
    except ImportError:
        return False
    return True


def setup_telemetry(config: FleetConfig):
    """Configure global OTLP meter/tracer providers from ``config``.

    Returns ``(tracer, meter)`` or ``None`` when telemetry is disabled
    (no endpoint) or the ``[otel]`` extra is not installed. Idempotent only at
    the caller's discretion — call once per process.
    """
    if not config.otlp_endpoint:
        return None
    if not _otel_available():
        raise RuntimeError(
            "otlp_endpoint set but the 'otel' extra is not installed; "
            "install mcp-fleet-core[otel]"
        )

    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    attrs = {"service.name": config.server_name}
    if config.service_version:
        attrs["service.version"] = config.service_version
    resource = Resource.create(attrs)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=config.otlp_endpoint))
    )
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=config.otlp_endpoint),
        export_interval_millis=config.metric_export_interval_ms,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    return trace.get_tracer("mcp_fleet_core"), metrics.get_meter("mcp_fleet_core")


def install_telemetry(mcp: object, config: FleetConfig, *, tracer=None, meter=None) -> None:
    """Wrap each tool so calls emit an OTEL span + metrics with server lineage.

    ``tracer``/``meter`` may be injected (tests); otherwise the global providers
    are used. No-op if neither is resolvable.
    """
    if tracer is None or meter is None:
        if not _otel_available():
            return
        from opentelemetry import metrics, trace

        tracer = tracer or trace.get_tracer("mcp_fleet_core")
        meter = meter or metrics.get_meter("mcp_fleet_core")

    calls = meter.create_counter(CALLS, description="Tool call count")
    duration = meter.create_histogram(DURATION, unit="ms", description="Tool call duration")
    errors = meter.create_counter(ERRORS, description="Tool call errors")

    tools = mcp._tool_manager._tools  # type: ignore[attr-defined]

    def _attrs(name: str) -> dict[str, str]:
        return {"mcp.server.name": config.server_name, "mcp.tool.name": name}

    def _record(name: str, start: float, error: bool) -> None:
        attrs = _attrs(name)
        duration.record(round((time.perf_counter() - start) * 1000, 2), attrs)
        calls.add(1, attrs)
        if error:
            errors.add(1, attrs)

    for name, tool in tools.items():
        original = tool.fn

        if inspect.iscoroutinefunction(original):

            @functools.wraps(original)
            async def awrapped(*args, __fn=original, __name=name, **kwargs):
                start = time.perf_counter()
                error = False
                with tracer.start_as_current_span("mcp.tool.call") as span:
                    span.set_attribute("mcp.server.name", config.server_name)
                    span.set_attribute("mcp.tool.name", __name)
                    try:
                        return await __fn(*args, **kwargs)
                    except Exception as exc:
                        error = True
                        span.record_exception(exc)
                        raise
                    finally:
                        _record(__name, start, error)

            tool.fn = awrapped
        else:

            @functools.wraps(original)
            def wrapped(*args, __fn=original, __name=name, **kwargs):
                start = time.perf_counter()
                error = False
                with tracer.start_as_current_span("mcp.tool.call") as span:
                    span.set_attribute("mcp.server.name", config.server_name)
                    span.set_attribute("mcp.tool.name", __name)
                    try:
                        return __fn(*args, **kwargs)
                    except Exception as exc:
                        error = True
                        span.record_exception(exc)
                        raise
                    finally:
                        _record(__name, start, error)

            tool.fn = wrapped
