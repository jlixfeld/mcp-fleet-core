"""OTEL tool-call instrumentation — asserted via in-memory exporters."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mcp_fleet_core.config import FleetConfig
from mcp_fleet_core.telemetry import CALLS, DURATION, ERRORS, install_telemetry


class _Tool:
    def __init__(self, fn):
        self.fn = fn


class _Manager:
    def __init__(self, tools):
        self._tools = tools


class _FakeMCP:
    def __init__(self, tools):
        self._tool_manager = _Manager(tools)


def _harness():
    span_exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(span_exporter))
    reader = InMemoryMetricReader()
    mp = MeterProvider(metric_readers=[reader])
    return tp.get_tracer("t"), mp.get_meter("m"), span_exporter, reader


def _metric_points(reader):
    """Flatten exported metrics into {name: [(value, attrs), ...]}."""
    out: dict[str, list] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                for pt in metric.data.data_points:
                    value = getattr(pt, "value", getattr(pt, "sum", None))
                    out.setdefault(metric.name, []).append((value, dict(pt.attributes)))
    return out


@pytest.mark.asyncio
async def test_async_tool_emits_span_and_metrics() -> None:
    tracer, meter, spans, reader = _harness()

    async def do_thing():
        return {"ok": True}

    mcp = _FakeMCP({"do_thing": _Tool(do_thing)})
    cfg = FleetConfig(server_name="strattrader", auth_mode="off")
    install_telemetry(mcp, cfg, tracer=tracer, meter=meter)

    result = await mcp._tool_manager._tools["do_thing"].fn()
    assert result == {"ok": True}

    # Span: name + lineage attributes.
    finished = spans.get_finished_spans()
    assert len(finished) == 1
    span = finished[0]
    assert span.name == "mcp.tool.call"
    assert span.attributes["mcp.server.name"] == "strattrader"
    assert span.attributes["mcp.tool.name"] == "do_thing"

    # Metrics: calls counted, duration recorded, no errors. Attributes carry lineage.
    points = _metric_points(reader)
    assert points[CALLS][0][0] == 1
    assert points[CALLS][0][1] == {"mcp.server.name": "strattrader", "mcp.tool.name": "do_thing"}
    assert DURATION in points
    assert ERRORS not in points  # error counter only materializes on failure


def test_sync_tool_error_records_error_metric() -> None:
    tracer, meter, spans, reader = _harness()

    def boom():
        raise ValueError("nope")

    mcp = _FakeMCP({"boom": _Tool(boom)})
    cfg = FleetConfig(server_name="srv", auth_mode="off")
    install_telemetry(mcp, cfg, tracer=tracer, meter=meter)

    with pytest.raises(ValueError, match="nope"):
        mcp._tool_manager._tools["boom"].fn()

    points = _metric_points(reader)
    assert points[CALLS][0][0] == 1
    assert points[ERRORS][0][0] == 1
    assert points[ERRORS][0][1] == {"mcp.server.name": "srv", "mcp.tool.name": "boom"}
    span = spans.get_finished_spans()[0]
    assert any(e.name == "exception" for e in span.events)


def test_no_endpoint_setup_is_noop() -> None:
    from mcp_fleet_core.telemetry import setup_telemetry

    cfg = FleetConfig(server_name="srv", auth_mode="off")  # otlp_endpoint=None
    assert setup_telemetry(cfg) is None
