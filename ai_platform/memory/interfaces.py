from typing import Protocol

from ai_platform.common.schemas import ChatMessage


class MemoryStore(Protocol):
    """What Runtime needs to persist and reload conversation history across
    requests, keyed by a caller-supplied conversation_id. Runtime depends on
    this interface only, never a concrete storage backend — the same DI
    pattern already used for ModelProvider and the Tool protocol."""

    async def load(self, conversation_id: str) -> list[ChatMessage]: ...

    async def append(self, conversation_id: str, messages: list[ChatMessage]) -> None: ...
