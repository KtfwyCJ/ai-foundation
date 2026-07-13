from typing import Protocol

from ai_platform.common.schemas import ChatResponse
from ai_platform.evaluation.types import EvalCase, GradeResult


class Grader(Protocol):
    """What EvalRunner needs to turn a response into a verdict. EvalRunner
    depends on this interface only, never a concrete grading strategy — the
    same DI pattern already used for ModelProvider, ToolRegistry,
    MemoryStore, and Tracer. Swapping ContainsGrader for an LLM-as-judge
    grader later is a new class, not a change to EvalRunner."""

    async def grade(self, response: ChatResponse, case: EvalCase) -> GradeResult: ...
