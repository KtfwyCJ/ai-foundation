from ai_platform.common.interfaces import RuntimeClient
from ai_platform.common.schemas import ChatRequest
from ai_platform.evaluation.interfaces import Grader
from ai_platform.evaluation.types import EvalCase, EvalResult, EvalSummary


class EvalRunner:
    """Runs a set of EvalCases through a RuntimeClient and grades each
    response with a Grader. Depends on RuntimeClient — the Gateway's own
    interface, not the concrete RuntimeEngine — so eval can run against a
    real wired Runtime or a test double identically. A case whose request
    itself raises is recorded as an error, not a failed grade: the two are
    different signals (the system broke vs. the system answered, just
    wrong), and collapsing them would hide which one happened."""

    def __init__(self, runtime: RuntimeClient, grader: Grader) -> None:
        self._runtime = runtime
        self._grader = grader

    async def run(self, cases: list[EvalCase]) -> list[EvalResult]:
        results = []
        for case in cases:
            results.append(await self._run_one(case))
        return results

    async def _run_one(self, case: EvalCase) -> EvalResult:
        request = ChatRequest(messages=case.messages, model=case.model)
        try:
            response = await self._runtime.handle_chat(request)
        except Exception as exc:
            return EvalResult(case_id=case.id, error=str(exc))

        grade = await self._grader.grade(response, case)
        response_text = response.message.content if isinstance(response.message.content, str) else None
        return EvalResult(case_id=case.id, response_text=response_text, grade=grade)


def summarize(results: list[EvalResult]) -> EvalSummary:
    """Aggregates a list of EvalResults into pass/fail/error counts and a
    pass rate. A pure function, not a method on EvalRunner, since it only
    needs the results list — no reason to couple it to a runtime instance."""
    total = len(results)
    errored = sum(1 for r in results if r.error is not None)
    passed = sum(1 for r in results if r.grade is not None and r.grade.passed)
    failed = total - passed - errored
    return EvalSummary(
        total=total,
        passed=passed,
        failed=failed,
        errored=errored,
        pass_rate=(passed / total) if total else 0.0,
    )
