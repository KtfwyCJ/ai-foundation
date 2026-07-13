from ai_platform.common.errors import RuntimeToolLoopExceededError
from ai_platform.common.schemas import ChatMessage, ChatRequest, ChatResponse, ToolResultBlock
from ai_platform.memory.interfaces import MemoryStore
from ai_platform.providers.interfaces import ModelProvider
from ai_platform.providers.types import ToolCall
from ai_platform.tools.registry import ToolRegistry

_MAX_TOOL_ITERATIONS = 5


class RuntimeEngine:
    """RuntimeClient implementation that composes a ModelProvider, a
    ToolRegistry, and (optionally) a MemoryStore into the Gateway-facing
    chat contract. Owns the tool-calling loop: call the model, and if it
    asks for a tool, execute it and call again — up to a hard iteration cap,
    since an ever-looping model is a real failure mode, not a hypothetical
    one. When request.conversation_id is set and a MemoryStore is present,
    prior turns are loaded before the loop and every new turn (including
    any tool exchange) is persisted after it. ProviderError is deliberately
    not caught here: it's already on the platform's PlatformError hierarchy
    and the Gateway's error handler maps it to HTTP by type, so it
    propagates unchanged rather than being re-wrapped."""

    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self._provider = provider
        self._tools = tools or ToolRegistry()
        self._memory = memory

    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
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
            result = await self._provider.complete(list(messages), model=request.model, tools=tool_definitions)

            if not result.tool_calls:
                messages.append(result.message)
                await self._persist_new_turns(request, history, messages)
                return ChatResponse(message=result.message, model=request.model)

            messages.append(result.message)
            messages.append(await self._run_tool_calls(result.tool_calls))

        raise RuntimeToolLoopExceededError(
            f"Exceeded {_MAX_TOOL_ITERATIONS} tool-call iterations without a final answer"
        )

    async def _persist_new_turns(
        self,
        request: ChatRequest,
        history: list[ChatMessage],
        messages: list[ChatMessage],
    ) -> None:
        if request.conversation_id and self._memory:
            await self._memory.append(request.conversation_id, messages[len(history) :])

    async def _run_tool_calls(self, tool_calls: list[ToolCall]) -> ChatMessage:
        results = []
        for call in tool_calls:
            tool = self._tools.get(call.name)
            output = await tool.execute(**call.input)
            results.append(ToolResultBlock(tool_use_id=call.id, content=output))
        return ChatMessage(role="user", content=results)
