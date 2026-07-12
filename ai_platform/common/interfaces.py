from typing import Protocol

from ai_platform.common.schemas import ChatRequest, ChatResponse


class RuntimeClient(Protocol):
    """What the Gateway needs from Runtime. The Gateway depends on this
    interface only, never on a concrete Runtime implementation — this is
    what lets Gateway and Runtime be built, tested, and deployed independently."""

    async def handle_chat(self, request: ChatRequest) -> ChatResponse: ...
