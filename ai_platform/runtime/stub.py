from ai_platform.common.schemas import ChatMessage, ChatRequest, ChatResponse


class EchoRuntimeClient:
    """Placeholder RuntimeClient so the Gateway is runnable before the real
    Runtime module exists. Echoes the last user message back. Delete this
    once Runtime is built and wire api/dependencies.py to the real client."""

    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
        last_user_message = request.messages[-1].content
        return ChatResponse(
            message=ChatMessage(role="assistant", content=f"echo: {last_user_message}"),
            model=request.model,
        )
