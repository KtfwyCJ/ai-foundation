import time
import uuid

from ai_platform.common.errors import RuntimeToolLoopExceededError
from ai_platform.common.schemas import ChatMessage, ChatRequest, ChatResponse, ToolDefinition, ToolResultBlock
from ai_platform.memory.interfaces import MemoryStore
from ai_platform.providers.interfaces import ModelProvider
from ai_platform.providers.types import ProviderResponse, ToolCall
from ai_platform.sandbox.interfaces import Sandbox
from ai_platform.tools.registry import ToolRegistry
from ai_platform.tracing.interfaces import Tracer
from ai_platform.tracing.types import Span

_MAX_TOOL_ITERATIONS = 5


class RuntimeEngine:
    """RuntimeClient implementation that composes a ModelProvider, a
    ToolRegistry, and (optionally) a MemoryStore and a Tracer into the
    Gateway-facing chat contract. Owns the tool-calling loop: call the
    model, and if it asks for a tool, execute it and call again — up to a
    hard iteration cap, since an ever-looping model is a real failure mode,
    not a hypothetical one. When request.conversation_id is set and a
    MemoryStore is present, prior turns are loaded before the loop and every
    new turn (including any tool exchange) is persisted after it. When a
    Tracer is present, every provider call and tool execution is recorded as
    a Span under a trace_id (the conversation_id when one exists, otherwise
    a per-request id) — this is the only place request-level timing and
    token usage are ever observed, so it lives here rather than inside
    ModelProvider or ToolRegistry, neither of which know they're part of a
    request. ProviderError is deliberately not caught here: it's already on
    the platform's PlatformError hierarchy and the Gateway's error handler
    maps it to HTTP by type, so it propagates unchanged rather than being
    re-wrapped. When a Sandbox is present, tool execution is routed through
    it instead of calling Tool.execute directly — a model chooses a tool
    call's arguments, so that call is untrusted input, and the Sandbox is
    what enforces a timeout and memory ceiling on it. Absent a Sandbox,
    behavior is unchanged from before Sandbox existed."""

    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry | None = None,
        memory: MemoryStore | None = None,
        tracer: Tracer | None = None,
        sandbox: Sandbox | None = None,
    ) -> None:
        self._provider = provider
        self._tools = tools or ToolRegistry()
        self._memory = memory
        self._tracer = tracer
        self._sandbox = sandbox

    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
        trace_id = request.conversation_id or str(uuid.uuid4())
        history: list[ChatMessage] = []
        if request.conversation_id and self._memory:
            history = await self._memory.load(request.conversation_id)

        messages = history + list(request.messages)
        tool_definitions = self._tools.definitions()

        for _ in range(_MAX_TOOL_ITERATIONS):
            # Pass a snapshot: messages is mutated after this call (tool
            # exchange turns, or the final reply for persistence), and a
            # provider must see exactly what was sent for *this* call, not a
            # live view that changes underneath it.
            result = await self._complete(trace_id, list(messages), request.model, tool_definitions)

            if not result.tool_calls:
                messages.append(result.message)
                await self._persist_new_turns(request, history, messages)
                return ChatResponse(message=result.message, model=request.model)

            messages.append(result.message)
            messages.append(await self._run_tool_calls(trace_id, result.tool_calls))

        raise RuntimeToolLoopExceededError(
            f"Exceeded {_MAX_TOOL_ITERATIONS} tool-call iterations without a final answer"
        )

    async def _complete(
        self,
        trace_id: str,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition],
    ) -> ProviderResponse:
        start = time.monotonic()
        try:
            result = await self._provider.complete(messages, model=model, tools=tools)
        except Exception as exc:
            await self._record_span(trace_id, "provider.complete", start, {"model": model}, error=str(exc))
            raise
        await self._record_span(
            trace_id,
            "provider.complete",
            start,
            {
                "model": model,
                "stop_reason": result.stop_reason,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
        )
        return result

    async def _persist_new_turns(
        self,
        request: ChatRequest,
        history: list[ChatMessage],
        messages: list[ChatMessage],
    ) -> None:
        if request.conversation_id and self._memory:
            await self._memory.append(request.conversation_id, messages[len(history) :])

    async def _run_tool_calls(self, trace_id: str, tool_calls: list[ToolCall]) -> ChatMessage:
        results = []
        for call in tool_calls:
            start = time.monotonic()
            try:
                tool = self._tools.get(call.name)
                if self._sandbox:
                    output = (await self._sandbox.run(tool, call.input)).output
                else:
                    output = await tool.execute(**call.input)
            except Exception as exc:
                await self._record_span(trace_id, "tool.execute", start, {"tool": call.name}, error=str(exc))
                raise
            await self._record_span(trace_id, "tool.execute", start, {"tool": call.name})
            results.append(ToolResultBlock(tool_use_id=call.id, content=output))
        return ChatMessage(role="user", content=results)

    async def _record_span(
        self,
        trace_id: str,
        name: str,
        start: float,
        attributes: dict,
        error: str | None = None,
    ) -> None:
        if not self._tracer:
            return
        await self._tracer.record(
            Span(
                trace_id=trace_id,
                name=name,
                duration_ms=(time.monotonic() - start) * 1000,
                attributes=attributes,
                error=error,
            )
        )
