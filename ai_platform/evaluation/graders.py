from ai_platform.common.schemas import ChatResponse
from ai_platform.evaluation.types import EvalCase, GradeResult


class ContainsGrader:
    """Example Grader implementation: passes if the response's text content
    contains case.expected, case-insensitive. Exists to exercise the eval
    harness end-to-end with something deterministic and easy to reason
    about — not meant to be the platform's only grading strategy, the same
    role CalculatorTool plays for the Tool Registry."""

    async def grade(self, response: ChatResponse, case: EvalCase) -> GradeResult:
        text = response.message.content if isinstance(response.message.content, str) else ""
        passed = case.expected.lower() in text.lower()
        return GradeResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            detail=f"expected {case.expected!r} in response text, got {text!r}",
        )
