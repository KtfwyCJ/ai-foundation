from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    """One ordered step in a Plan: a natural-language description of what
    should happen, and an optional hint at which registered tool (if any)
    the step is expected to use."""

    description: str
    tool_hint: str | None = None


class Plan(BaseModel):
    """An ordered decomposition of a request into steps, produced by a
    Planner before RuntimeEngine's reactive tool-calling loop runs. v0.1 is
    observational only — RuntimeEngine records a Plan on the trace but does
    not change execution based on it (see planning/interfaces.py)."""

    steps: list[PlanStep] = Field(default_factory=list)
