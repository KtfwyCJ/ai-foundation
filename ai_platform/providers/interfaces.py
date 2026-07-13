from typing import Protocol

from ai_platform.common.schemas import ChatMessage, ToolDefinition
from ai_platform.providers.types import ProviderResponse


class ModelProvider(Protocol):
    """What Runtime needs from a model provider. Runtime depends on this
    interface only, never on a concrete SDK client — this is what lets a
    second provider be added later without touching Runtime."""

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int = 1024,
        tools: list[ToolDefinition] | None = None,
    ) -> ProviderResponse: ...
