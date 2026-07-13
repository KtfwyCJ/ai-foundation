from pydantic import BaseModel, Field


class Span(BaseModel):
    """One traced unit of work inside a request — a single provider call or
    tool execution. Grouped under trace_id (the conversation_id when one
    exists, otherwise a per-request id), so every span belonging to the same
    request or conversation can be retrieved together. Carries enough to
    answer "what happened on this request" without a live model call to
    find out: what ran, how long it took, and — for a provider call —
    the token counts ProviderResponse has exposed since the Provider module
    but that nothing has read until now."""

    trace_id: str
    name: str
    duration_ms: float
    attributes: dict = Field(default_factory=dict)
    error: str | None = None
