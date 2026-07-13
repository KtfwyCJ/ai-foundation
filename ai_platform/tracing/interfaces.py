from typing import Protocol

from ai_platform.tracing.types import Span


class Tracer(Protocol):
    """What Runtime needs to record what happened on a request, one span at
    a time. Runtime depends on this interface only, never a concrete sink —
    the same DI pattern already used for ModelProvider, ToolRegistry, and
    MemoryStore."""

    async def record(self, span: Span) -> None: ...
