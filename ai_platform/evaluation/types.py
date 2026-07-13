from pydantic import BaseModel

from ai_platform.common.schemas import ChatMessage


class EvalCase(BaseModel):
    """One test case: an input conversation, the model to run it against,
    and an expectation a Grader will check the response against. `expected`
    is deliberately a plain string rather than a typed field, since its
    meaning is entirely up to whichever Grader runs the case (a substring
    for ContainsGrader today, a rubric for an LLM-as-judge grader later)."""

    id: str
    messages: list[ChatMessage]
    expected: str
    model: str = "claude-sonnet-5"


class GradeResult(BaseModel):
    """A Grader's verdict on one response. score is a float (not just
    passed: bool) so a future partial-credit grader (e.g. an LLM judge
    scoring 0-1 on rubric adherence) fits the same shape a deterministic
    pass/fail grader uses today."""

    passed: bool
    score: float
    detail: str


class EvalResult(BaseModel):
    """The outcome of running one EvalCase: either a graded response, or
    an error if the request itself failed before grading was possible.
    Exactly one of grade/error is set."""

    case_id: str
    response_text: str | None = None
    grade: GradeResult | None = None
    error: str | None = None


class EvalSummary(BaseModel):
    """Aggregate result across an EvalRunner.run() call. Kept separate from
    the per-case EvalResult list so callers that just want a pass rate
    don't have to recompute it from raw results themselves."""

    total: int
    passed: int
    failed: int
    errored: int
    pass_rate: float
