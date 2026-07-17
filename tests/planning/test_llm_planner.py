from ai_platform.common.schemas import ChatMessage, ChatRequest, ToolDefinition
from ai_platform.planning.llm_planner import LLMPlanner
from ai_platform.providers.types import ProviderResponse


class FakePlanningProvider:
    """Stands in for a ModelProvider for LLMPlanner tests only — records the
    messages/model it was called with and returns a scripted text response,
    the same shape a real Anthropic completion's message.content would take
    once no tool_use blocks are present."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict] = []

    async def complete(self, messages, *, model, max_tokens=1024, tools=None):
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        return ProviderResponse(
            message=ChatMessage(role="assistant", content=self._content),
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )


def _request(content: str = "plan my trip") -> ChatRequest:
    return ChatRequest(messages=[ChatMessage(role="user", content=content)])


async def test_plan_parses_a_json_array_of_steps():
    provider = FakePlanningProvider(
        '[{"description": "search flights", "tool_hint": "flight_search"}, '
        '{"description": "book the cheapest option"}]'
    )
    planner = LLMPlanner(provider)

    plan = await planner.plan(_request(), tools=[])

    assert len(plan.steps) == 2
    assert plan.steps[0].description == "search flights"
    assert plan.steps[0].tool_hint == "flight_search"
    assert plan.steps[1].description == "book the cheapest option"
    assert plan.steps[1].tool_hint is None


async def test_plan_sends_a_system_prompt_and_the_requests_messages():
    provider = FakePlanningProvider("[]")
    planner = LLMPlanner(provider)
    request = _request("plan my trip")

    await planner.plan(request, tools=[])

    sent = provider.calls[0]
    assert sent["model"] == request.model
    assert sent["messages"][0].role == "system"
    assert sent["messages"][1] == request.messages[0]


async def test_plan_grounds_tool_hints_by_naming_registered_tools_in_the_prompt():
    provider = FakePlanningProvider("[]")
    planner = LLMPlanner(provider)
    tools = [ToolDefinition(name="calculator", description="Evaluates arithmetic expressions", input_schema={})]

    await planner.plan(_request(), tools=tools)

    system_content = provider.calls[0]["messages"][0].content
    assert "calculator" in system_content
    assert "Evaluates arithmetic expressions" in system_content


async def test_plan_does_not_forward_tools_to_the_provider_call():
    """Real tool definitions must not reach ModelProvider.complete()'s own
    `tools` argument here — that would let the model return an actual
    tool_use block instead of the JSON plan text this parser expects."""
    provider = FakePlanningProvider("[]")
    planner = LLMPlanner(provider)
    tools = [ToolDefinition(name="calculator", description="Evaluates arithmetic expressions", input_schema={})]

    await planner.plan(_request(), tools=tools)

    assert provider.calls[0]["tools"] is None


async def test_plan_tells_the_model_when_no_tools_are_registered():
    provider = FakePlanningProvider("[]")
    planner = LLMPlanner(provider)

    await planner.plan(_request(), tools=[])

    assert "no tools are registered" in provider.calls[0]["messages"][0].content.lower()


async def test_plan_parses_json_wrapped_in_a_markdown_code_fence():
    provider = FakePlanningProvider('```json\n[{"description": "search flights"}]\n```')
    planner = LLMPlanner(provider)

    plan = await planner.plan(_request(), tools=[])

    assert len(plan.steps) == 1
    assert plan.steps[0].description == "search flights"


async def test_plan_returns_an_empty_plan_when_response_is_not_valid_json():
    planner = LLMPlanner(FakePlanningProvider("sure, here's a plan: first, ..."))

    plan = await planner.plan(_request(), tools=[])

    assert plan.steps == []


async def test_plan_returns_an_empty_plan_when_response_is_not_a_json_array():
    planner = LLMPlanner(FakePlanningProvider('{"description": "not a list"}'))

    plan = await planner.plan(_request(), tools=[])

    assert plan.steps == []


async def test_plan_skips_array_items_missing_a_description():
    provider = FakePlanningProvider('[{"tool_hint": "no_description_here"}, {"description": "valid step"}]')
    planner = LLMPlanner(provider)

    plan = await planner.plan(_request(), tools=[])

    assert len(plan.steps) == 1
    assert plan.steps[0].description == "valid step"
