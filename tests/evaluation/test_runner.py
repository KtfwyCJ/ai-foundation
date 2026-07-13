from ai_platform.common.errors import ProviderTimeoutError
from ai_platform.common.schemas import ChatMessage, ChatResponse
from ai_platform.evaluation.graders import ContainsGrader
from ai_platform.evaluation.runner import EvalRunner, summarize
from ai_platform.evaluation.types import EvalCase

from .conftest import FakeRuntimeClient


def _case(case_id: str, expected: str) -> EvalCase:
    return EvalCase(id=case_id, messages=[ChatMessage(role="user", content="hi")], expected=expected)


async def test_run_grades_each_case_against_its_response():
    runtime = FakeRuntimeClient(
        responses=[
            ChatResponse(message=ChatMessage(role="assistant", content="5"), model="claude-sonnet-5"),
            ChatResponse(message=ChatMessage(role="assistant", content="wrong"), model="claude-sonnet-5"),
        ]
    )
    runner = EvalRunner(runtime, ContainsGrader())
    cases = [_case("c1", "5"), _case("c2", "5")]

    results = await runner.run(cases)

    assert [r.case_id for r in results] == ["c1", "c2"]
    assert results[0].grade.passed is True
    assert results[1].grade.passed is False
    assert results[0].error is None


async def test_run_records_an_error_result_when_runtime_raises():
    runtime = FakeRuntimeClient(responses=[], errors={0: ProviderTimeoutError("boom")})
    runner = EvalRunner(runtime, ContainsGrader())

    results = await runner.run([_case("c1", "5")])

    assert results[0].error == "boom"
    assert results[0].grade is None


async def test_run_passes_each_cases_messages_and_model_to_runtime():
    runtime = FakeRuntimeClient(
        responses=[ChatResponse(message=ChatMessage(role="assistant", content="5"), model="claude-opus-4-8")]
    )
    runner = EvalRunner(runtime, ContainsGrader())
    case = EvalCase(
        id="c1",
        messages=[ChatMessage(role="user", content="what is 2+3?")],
        expected="5",
        model="claude-opus-4-8",
    )

    await runner.run([case])

    assert runtime.requests[0].messages == case.messages
    assert runtime.requests[0].model == "claude-opus-4-8"


async def test_summarize_computes_pass_fail_error_counts_and_rate():
    runtime = FakeRuntimeClient(
        responses=[
            ChatResponse(message=ChatMessage(role="assistant", content="5"), model="claude-sonnet-5"),
            ChatResponse(message=ChatMessage(role="assistant", content="wrong"), model="claude-sonnet-5"),
        ],
        errors={2: ProviderTimeoutError("boom")},
    )
    runner = EvalRunner(runtime, ContainsGrader())
    cases = [_case("c1", "5"), _case("c2", "5"), _case("c3", "5")]

    results = await runner.run(cases)
    summary = summarize(results)

    assert summary.total == 3
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.errored == 1
    assert summary.pass_rate == 1 / 3


async def test_summarize_returns_zero_pass_rate_for_empty_results():
    summary = summarize([])

    assert summary.total == 0
    assert summary.pass_rate == 0.0
