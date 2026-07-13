from ai_platform.tracing.types import Span


class InMemoryTracer:
    """List-backed Tracer, keyed by trace_id — single-process only, same
    limitation as InMemoryStore. Exists so Runtime's tracing calls have a
    real, testable sink from day one instead of a stub, and so a request's
    spans can be inspected after the fact via get_trace()."""

    def __init__(self) -> None:
        self._traces: dict[str, list[Span]] = {}

    async def record(self, span: Span) -> None:
        self._traces.setdefault(span.trace_id, []).append(span)

    async def get_trace(self, trace_id: str) -> list[Span]:
        return list(self._traces.get(trace_id, []))
