from ai_platform.common.schemas import ChatMessage


class InMemoryStore:
    """Dict-backed MemoryStore — single-process only. Like the Gateway's
    in-memory rate limiter, this doesn't share state across multiple
    Runtime/Gateway processes; move to Redis or a database the moment
    there's more than one replica."""

    def __init__(self) -> None:
        self._conversations: dict[str, list[ChatMessage]] = {}

    async def load(self, conversation_id: str) -> list[ChatMessage]:
        return list(self._conversations.get(conversation_id, []))

    async def append(self, conversation_id: str, messages: list[ChatMessage]) -> None:
        self._conversations.setdefault(conversation_id, []).extend(messages)
