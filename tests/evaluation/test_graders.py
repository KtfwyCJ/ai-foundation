from ai_platform.common.schemas import ChatMessage, ChatResponse
from ai_platform.evaluation.graders import ContainsGrader
from ai_platform.evaluation.types import EvalCase


async def test_contains_grader_passes_when_expected_text_present():
    grader = ContainsGrader()
    case = EvalCase(id="c1", messages=[ChatMessage(role="user", content="2+3?")], expected="5")
    response = ChatResponse(message=ChatMessage(role="assistant", content="The answer is 5."), model="claude-sonnet-5", trace_id="trace-1")

    grade = await grader.grade(response, case)

    assert grade.passed is True
    assert grade.score == 1.0


async def test_contains_grader_fails_when_expected_text_absent():
    grader = ContainsGrader()
    case = EvalCase(id="c1", messages=[ChatMessage(role="user", content="2+3?")], expected="5")
    response = ChatResponse(message=ChatMessage(role="assistant", content="The answer is 42."), model="claude-sonnet-5", trace_id="trace-1")

    grade = await grader.grade(response, case)

    assert grade.passed is False
    assert grade.score == 0.0


async def test_contains_grader_is_case_insensitive():
    grader = ContainsGrader()
    case = EvalCase(id="c1", messages=[ChatMessage(role="user", content="hi")], expected="HELLO")
    response = ChatResponse(message=ChatMessage(role="assistant", content="hello there"), model="claude-sonnet-5", trace_id="trace-1")

    grade = await grader.grade(response, case)

    assert grade.passed is True


async def test_contains_grader_fails_gracefully_on_block_content():
    grader = ContainsGrader()
    case = EvalCase(id="c1", messages=[ChatMessage(role="user", content="hi")], expected="hello")
    response = ChatResponse(message=ChatMessage(role="assistant", content=[]), model="claude-sonnet-5", trace_id="trace-1")

    grade = await grader.grade(response, case)

    assert grade.passed is False
