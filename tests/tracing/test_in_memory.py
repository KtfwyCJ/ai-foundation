from ai_platform.tracing.in_memory import InMemoryTracer
from ai_platform.tracing.types import Span


async def test_get_trace_returns_empty_list_for_unknown_trace_id():
    tracer = InMemoryTracer()

    assert await tracer.get_trace("does-not-exist") == []


async def test_record_then_get_trace_returns_the_recorded_span():
    tracer = InMemoryTracer()
    span = Span(trace_id="trace-1", name="provider.complete", duration_ms=12.5, attributes={"model": "x"})

    await tracer.record(span)

    assert await tracer.get_trace("trace-1") == [span]


async def test_multiple_records_accumulate_in_order():
    tracer = InMemoryTracer()
    first = Span(trace_id="trace-1", name="provider.complete", duration_ms=1.0)
    second = Span(trace_id="trace-1", name="tool.execute", duration_ms=2.0)

    await tracer.record(first)
    await tracer.record(second)

    assert await tracer.get_trace("trace-1") == [first, second]


async def test_traces_are_isolated_by_trace_id():
    tracer = InMemoryTracer()
    await tracer.record(Span(trace_id="trace-1", name="a", duration_ms=1.0))
    await tracer.record(Span(trace_id="trace-2", name="b", duration_ms=1.0))

    assert await tracer.get_trace("trace-1") != await tracer.get_trace("trace-2")


async def test_get_trace_returns_a_copy_not_the_internal_list():
    tracer = InMemoryTracer()
    await tracer.record(Span(trace_id="trace-1", name="a", duration_ms=1.0))

    fetched = await tracer.get_trace("trace-1")
    fetched.append(Span(trace_id="trace-1", name="mutated", duration_ms=1.0))

    assert len(await tracer.get_trace("trace-1")) == 1
