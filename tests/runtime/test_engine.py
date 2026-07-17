import pytest

from ai_platform.common.errors import ProviderTimeoutError, RuntimeToolLoopExceededError, ToolNotFoundError
from ai_platform.common.schemas import ChatMessage, ChatRequest, ToolResultBlock, ToolUseBlock
from ai_platform.memory.in_memory import InMemoryStore
from ai_platform.planning.types import Plan, PlanStep
from ai_platform.providers.types import ProviderResponse, ToolCall
from ai_platform.runtime.engine import RuntimeEngine
from ai_platform.sandbox.types import SandboxResult
from ai_platform.tools.builtin import CalculatorTool
from ai_platform.tools.registry import ToolRegistry
from ai_platform.tracing.in_memory import InMemoryTracer

from .conftest import FakeModelProvider


class FakeSandbox:
    """Stands in for a Sandbox — records every tool passed to it and
    returns a scripted SandboxResult, so RuntimeEngine's routing decision
    (sandbox present vs absent) can be tested without a real subprocess."""

    def __init__(self, output: str = "sandboxed-5") -> None:
        self._output = output
        self.calls: list[tuple[object, dict]] = []

    async def run(self, tool, kwargs: dict) -> SandboxResult:
        self.calls.append((tool, kwargs))
        return SandboxResult(output=self._output, duration_ms=1.0)


async def test_handle_chat_returns_providers_message(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    engine = RuntimeEngine(provider)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hello")], model="claude-sonnet-5")

    response = await engine.handle_chat(request)

    assert response.message.role == "assistant"
    assert response.message.content == "hi there"
    assert response.model == "claude-sonnet-5"


async def test_handle_chat_passes_messages_and_model_to_provider(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    engine = RuntimeEngine(provider)
    messages = [
        ChatMessage(role="system", content="be terse"),
        ChatMessage(role="user", content="hello"),
    ]
    request = ChatRequest(messages=messages, model="claude-opus-4-8")

    await engine.handle_chat(request)

    assert provider.last_messages == messages
    assert provider.last_model == "claude-opus-4-8"


async def test_handle_chat_lets_provider_error_propagate():
    provider = FakeModelProvider(error=ProviderTimeoutError("boom"))
    engine = RuntimeEngine(provider)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])

    with pytest.raises(ProviderTimeoutError):
        await engine.handle_chat(request)


async def test_handle_chat_with_no_tools_registered_passes_empty_tool_list(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    engine = RuntimeEngine(provider)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])

    await engine.handle_chat(request)

    assert provider.last_tools == []


async def test_handle_chat_passes_registered_tool_definitions(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    engine = RuntimeEngine(provider, registry)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])

    await engine.handle_chat(request)

    assert [t.name for t in provider.last_tools] == ["calculator"]


async def test_handle_chat_executes_tool_call_and_completes_loop():
    tool_use = ToolUseBlock(id="call_1", name="calculator", input={"operation": "add", "a": 2, "b": 3})
    tool_call_response = ProviderResponse(
        message=ChatMessage(role="assistant", content=[tool_use]),
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
        tool_calls=[ToolCall(id="call_1", name="calculator", input={"operation": "add", "a": 2, "b": 3})],
    )
    final_response = ProviderResponse(
        message=ChatMessage(role="assistant", content="The answer is 5."),
        stop_reason="end_turn",
        input_tokens=15,
        output_tokens=6,
    )
    provider = FakeModelProvider(responses=[tool_call_response, final_response])
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    engine = RuntimeEngine(provider, registry)
    request = ChatRequest(messages=[ChatMessage(role="user", content="what is 2 + 3?")])

    response = await engine.handle_chat(request)

    assert response.message.content == "The answer is 5."
    assert len(provider.calls) == 2

    second_call_messages = provider.calls[1]["messages"]
    assert second_call_messages[-2].content == [tool_use]
    tool_result_message = second_call_messages[-1]
    assert tool_result_message.role == "user"
    assert tool_result_message.content == [ToolResultBlock(tool_use_id="call_1", content="5")]


async def test_handle_chat_raises_when_model_requests_unknown_tool():
    tool_use = ToolUseBlock(id="call_1", name="not_a_real_tool", input={})
    response = ProviderResponse(
        message=ChatMessage(role="assistant", content=[tool_use]),
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
        tool_calls=[ToolCall(id="call_1", name="not_a_real_tool", input={})],
    )
    provider = FakeModelProvider(response=response)
    engine = RuntimeEngine(provider, ToolRegistry())
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])

    with pytest.raises(ToolNotFoundError):
        await engine.handle_chat(request)


async def test_handle_chat_raises_when_tool_loop_never_converges():
    tool_use = ToolUseBlock(id="call_1", name="calculator", input={"operation": "add", "a": 1, "b": 1})
    response = ProviderResponse(
        message=ChatMessage(role="assistant", content=[tool_use]),
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
        tool_calls=[ToolCall(id="call_1", name="calculator", input={"operation": "add", "a": 1, "b": 1})],
    )
    provider = FakeModelProvider(response=response)
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    engine = RuntimeEngine(provider, registry)
    request = ChatRequest(messages=[ChatMessage(role="user", content="loop forever")])

    with pytest.raises(RuntimeToolLoopExceededError):
        await engine.handle_chat(request)


async def test_handle_chat_without_tool_calls_ignores_registry(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    engine = RuntimeEngine(provider, registry)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])

    response = await engine.handle_chat(request)

    assert response.message.content == "hi there"
    assert len(provider.calls) == 1


async def test_handle_chat_without_conversation_id_never_touches_memory(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    memory = InMemoryStore()
    engine = RuntimeEngine(provider, memory=memory)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])

    await engine.handle_chat(request)

    assert await memory.load("anything") == []


async def test_handle_chat_loads_prior_history_and_prepends_it(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    memory = InMemoryStore()
    await memory.append(
        "conv-1",
        [ChatMessage(role="user", content="earlier turn"), ChatMessage(role="assistant", content="earlier reply")],
    )
    engine = RuntimeEngine(provider, memory=memory)
    request = ChatRequest(
        messages=[ChatMessage(role="user", content="follow-up")],
        conversation_id="conv-1",
    )

    await engine.handle_chat(request)

    assert [m.content for m in provider.last_messages] == ["earlier turn", "earlier reply", "follow-up"]


async def test_handle_chat_persists_the_new_turn_after_replying(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    memory = InMemoryStore()
    engine = RuntimeEngine(provider, memory=memory)
    request = ChatRequest(
        messages=[ChatMessage(role="user", content="hi")],
        conversation_id="conv-1",
    )

    await engine.handle_chat(request)

    stored = await memory.load("conv-1")
    assert [m.content for m in stored] == ["hi", "hi there"]


async def test_handle_chat_persists_tool_exchange_messages():
    tool_use = ToolUseBlock(id="call_1", name="calculator", input={"operation": "add", "a": 2, "b": 3})
    tool_call_response = ProviderResponse(
        message=ChatMessage(role="assistant", content=[tool_use]),
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
        tool_calls=[ToolCall(id="call_1", name="calculator", input={"operation": "add", "a": 2, "b": 3})],
    )
    final_response = ProviderResponse(
        message=ChatMessage(role="assistant", content="The answer is 5."),
        stop_reason="end_turn",
        input_tokens=15,
        output_tokens=6,
    )
    provider = FakeModelProvider(responses=[tool_call_response, final_response])
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    memory = InMemoryStore()
    engine = RuntimeEngine(provider, registry, memory)
    request = ChatRequest(
        messages=[ChatMessage(role="user", content="what is 2 + 3?")],
        conversation_id="conv-1",
    )

    await engine.handle_chat(request)

    stored = await memory.load("conv-1")
    assert stored[0].content == "what is 2 + 3?"
    assert stored[1].content == [tool_use]
    assert stored[2].content == [ToolResultBlock(tool_use_id="call_1", content="5")]
    assert stored[3].content == "The answer is 5."


async def test_handle_chat_does_not_persist_when_tool_loop_exceeded():
    tool_use = ToolUseBlock(id="call_1", name="calculator", input={"operation": "add", "a": 1, "b": 1})
    response = ProviderResponse(
        message=ChatMessage(role="assistant", content=[tool_use]),
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
        tool_calls=[ToolCall(id="call_1", name="calculator", input={"operation": "add", "a": 1, "b": 1})],
    )
    provider = FakeModelProvider(response=response)
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    memory = InMemoryStore()
    engine = RuntimeEngine(provider, registry, memory)
    request = ChatRequest(messages=[ChatMessage(role="user", content="loop forever")], conversation_id="conv-1")

    with pytest.raises(RuntimeToolLoopExceededError):
        await engine.handle_chat(request)

    assert await memory.load("conv-1") == []


async def test_handle_chat_without_tracer_still_works(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    engine = RuntimeEngine(provider)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])

    response = await engine.handle_chat(request)

    assert response.message.content == "hi there"


async def test_handle_chat_records_a_provider_span(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    tracer = InMemoryTracer()
    engine = RuntimeEngine(provider, tracer=tracer)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")], conversation_id="conv-1")

    response = await engine.handle_chat(request)

    assert response.trace_id == "conv-1"
    spans = await tracer.get_trace("conv-1")
    assert len(spans) == 1
    assert spans[0].name == "provider.complete"
    assert spans[0].error is None
    assert spans[0].attributes["input_tokens"] == 12
    assert spans[0].attributes["output_tokens"] == 8
    assert spans[0].duration_ms >= 0


async def test_handle_chat_uses_a_generated_trace_id_when_no_conversation_id(fake_provider_response, monkeypatch):
    import uuid

    provider = FakeModelProvider(response=fake_provider_response)
    tracer = InMemoryTracer()
    engine = RuntimeEngine(provider, tracer=tracer)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])
    monkeypatch.setattr("ai_platform.runtime.engine.uuid.uuid4", lambda: "generated-id")

    response = await engine.handle_chat(request)

    assert response.trace_id == "generated-id"
    spans = await tracer.get_trace("generated-id")
    assert len(spans) == 1
    assert spans[0].name == "provider.complete"


async def test_handle_chat_records_a_span_per_tool_call():
    tool_use = ToolUseBlock(id="call_1", name="calculator", input={"operation": "add", "a": 2, "b": 3})
    tool_call_response = ProviderResponse(
        message=ChatMessage(role="assistant", content=[tool_use]),
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
        tool_calls=[ToolCall(id="call_1", name="calculator", input={"operation": "add", "a": 2, "b": 3})],
    )
    final_response = ProviderResponse(
        message=ChatMessage(role="assistant", content="The answer is 5."),
        stop_reason="end_turn",
        input_tokens=15,
        output_tokens=6,
    )
    provider = FakeModelProvider(responses=[tool_call_response, final_response])
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    tracer = InMemoryTracer()
    engine = RuntimeEngine(provider, registry, tracer=tracer)
    request = ChatRequest(messages=[ChatMessage(role="user", content="what is 2 + 3?")], conversation_id="conv-1")

    await engine.handle_chat(request)

    spans = await tracer.get_trace("conv-1")
    names = [span.name for span in spans]
    assert names == ["provider.complete", "tool.execute", "provider.complete"]
    assert spans[1].attributes["tool"] == "calculator"
    assert spans[1].error is None


async def test_handle_chat_records_an_error_span_when_provider_fails():
    provider = FakeModelProvider(error=ProviderTimeoutError("boom"))
    tracer = InMemoryTracer()
    engine = RuntimeEngine(provider, tracer=tracer)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")], conversation_id="conv-1")

    with pytest.raises(ProviderTimeoutError):
        await engine.handle_chat(request)

    spans = await tracer.get_trace("conv-1")
    assert len(spans) == 1
    assert spans[0].name == "provider.complete"
    assert spans[0].error == "boom"


async def test_handle_chat_routes_tool_execution_through_sandbox_when_present():
    tool_use = ToolUseBlock(id="call_1", name="calculator", input={"operation": "add", "a": 2, "b": 3})
    tool_call_response = ProviderResponse(
        message=ChatMessage(role="assistant", content=[tool_use]),
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
        tool_calls=[ToolCall(id="call_1", name="calculator", input={"operation": "add", "a": 2, "b": 3})],
    )
    final_response = ProviderResponse(
        message=ChatMessage(role="assistant", content="The answer is 5."),
        stop_reason="end_turn",
        input_tokens=15,
        output_tokens=6,
    )
    provider = FakeModelProvider(responses=[tool_call_response, final_response])
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    sandbox = FakeSandbox(output="sandboxed-5")
    engine = RuntimeEngine(provider, registry, sandbox=sandbox)
    request = ChatRequest(messages=[ChatMessage(role="user", content="what is 2 + 3?")])

    response = await engine.handle_chat(request)

    assert response.message.content == "The answer is 5."
    assert len(sandbox.calls) == 1
    called_tool, called_kwargs = sandbox.calls[0]
    assert called_tool.definition.name == "calculator"
    assert called_kwargs == {"operation": "add", "a": 2, "b": 3}

    second_call_messages = provider.calls[1]["messages"]
    tool_result_message = second_call_messages[-1]
    assert tool_result_message.content == [ToolResultBlock(tool_use_id="call_1", content="sandboxed-5")]


async def test_handle_chat_records_an_error_span_when_tool_not_found():
    tool_use = ToolUseBlock(id="call_1", name="not_a_real_tool", input={})
    response = ProviderResponse(
        message=ChatMessage(role="assistant", content=[tool_use]),
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
        tool_calls=[ToolCall(id="call_1", name="not_a_real_tool", input={})],
    )
    provider = FakeModelProvider(response=response)
    tracer = InMemoryTracer()
    engine = RuntimeEngine(provider, ToolRegistry(), tracer=tracer)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")], conversation_id="conv-1")

    with pytest.raises(ToolNotFoundError):
        await engine.handle_chat(request)

    spans = await tracer.get_trace("conv-1")
    tool_spans = [span for span in spans if span.name == "tool.execute"]
    assert len(tool_spans) == 1
    assert tool_spans[0].attributes["tool"] == "not_a_real_tool"
    assert "not_a_real_tool" in tool_spans[0].error


class FakePlanner:
    """Stands in for a Planner — returns a scripted Plan, or raises, so
    RuntimeEngine's observational-only planning step (record a span, never
    affect the tool loop) can be tested without a real LLMPlanner/provider
    call."""

    def __init__(self, plan: Plan | None = None, error: Exception | None = None) -> None:
        self._plan = plan or Plan()
        self._error = error
        self.calls: list[ChatRequest] = []

    async def plan(self, request, tools):
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        return self._plan


async def test_handle_chat_records_a_planner_span_when_planner_present(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    tracer = InMemoryTracer()
    plan = Plan(steps=[PlanStep(description="step one"), PlanStep(description="step two", tool_hint="calculator")])
    planner = FakePlanner(plan=plan)
    engine = RuntimeEngine(provider, tracer=tracer, planner=planner)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")], conversation_id="conv-1")

    await engine.handle_chat(request)

    assert planner.calls == [request]
    spans = await tracer.get_trace("conv-1")
    plan_spans = [span for span in spans if span.name == "planner.plan"]
    assert len(plan_spans) == 1
    assert plan_spans[0].error is None
    assert plan_spans[0].attributes["step_count"] == 2
    assert plan_spans[0].attributes["steps"] == [
        {"description": "step one", "tool_hint": None},
        {"description": "step two", "tool_hint": "calculator"},
    ]


async def test_handle_chat_without_planner_never_calls_one(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    tracer = InMemoryTracer()
    engine = RuntimeEngine(provider, tracer=tracer)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")], conversation_id="conv-1")

    await engine.handle_chat(request)

    spans = await tracer.get_trace("conv-1")
    assert all(span.name != "planner.plan" for span in spans)


async def test_handle_chat_still_completes_when_planner_raises(fake_provider_response):
    provider = FakeModelProvider(response=fake_provider_response)
    tracer = InMemoryTracer()
    planner = FakePlanner(error=ProviderTimeoutError("planning provider timed out"))
    engine = RuntimeEngine(provider, tracer=tracer, planner=planner)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")], conversation_id="conv-1")

    response = await engine.handle_chat(request)

    assert response.message.content == "hi there"
    spans = await tracer.get_trace("conv-1")
    plan_spans = [span for span in spans if span.name == "planner.plan"]
    assert len(plan_spans) == 1
    assert "planning provider timed out" in plan_spans[0].error
