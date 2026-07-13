from anthropic import (
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    AuthenticationError as AnthropicAuthenticationError,
    RateLimitError as AnthropicRateLimitError,
)

from ai_platform.common.config import Settings
from ai_platform.common.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from ai_platform.common.schemas import (
    ChatMessage,
    ContentBlock,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from ai_platform.providers.types import ProviderResponse, ToolCall


def _message_to_anthropic_turn(message: ChatMessage) -> dict:
    if isinstance(message.content, str):
        return {"role": message.role, "content": message.content}
    return {"role": message.role, "content": [_block_to_anthropic(block) for block in message.content]}


def _block_to_anthropic(block: ContentBlock) -> dict:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return {"type": "tool_result", "tool_use_id": block.tool_use_id, "content": block.content}


def _tool_to_anthropic(tool: ToolDefinition) -> dict:
    return {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}


def _parse_content_blocks(sdk_blocks) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for sdk_block in sdk_blocks:
        if sdk_block.type == "text":
            blocks.append(TextBlock(text=sdk_block.text))
        elif sdk_block.type == "tool_use":
            blocks.append(ToolUseBlock(id=sdk_block.id, name=sdk_block.name, input=sdk_block.input))
    return blocks


class AnthropicProvider:
    """ModelProvider implementation backed by the Anthropic SDK. Owns every
    Claude-specific translation: pulling system messages out of the generic
    message list (Anthropic takes `system` as a separate parameter, not a
    message with role="system"), translating ToolDefinition/ContentBlock
    to and from Claude's wire format, and mapping SDK exceptions onto the
    platform's transport-agnostic ProviderError hierarchy so callers never
    see an anthropic.* exception type."""

    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int = 1024,
        tools: list[ToolDefinition] | None = None,
    ) -> ProviderResponse:
        system_prompt = "\n".join(
            m.content for m in messages if m.role == "system" and isinstance(m.content, str)
        )
        turns = [_message_to_anthropic_turn(m) for m in messages if m.role != "system"]

        request_kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            messages=turns,
        )
        # Omit `system` entirely rather than passing None: the SDK's own
        # default for this parameter is its `Omit` sentinel (meaning "don't
        # serialize this key"), not None — passing a literal None serializes
        # to JSON `null`, which the API rejects against its `system: str |
        # array` schema instead of treating it as "no system prompt."
        if system_prompt:
            request_kwargs["system"] = system_prompt
        if tools:
            request_kwargs["tools"] = [_tool_to_anthropic(tool) for tool in tools]

        try:
            response = await self._client.messages.create(**request_kwargs)
        except AnthropicAuthenticationError as exc:
            raise ProviderAuthError("Anthropic rejected the API key") from exc
        except AnthropicRateLimitError as exc:
            raise ProviderRateLimitError("Anthropic rate limit exceeded") from exc
        except APITimeoutError as exc:
            raise ProviderTimeoutError("Anthropic did not respond in time") from exc
        except APIStatusError as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc

        blocks = _parse_content_blocks(response.content)
        tool_use_blocks = [block for block in blocks if isinstance(block, ToolUseBlock)]

        # Tool-call turns must echo the exact blocks back as conversation
        # history (Claude requires the tool_use block that a tool_result
        # answers); pure-text turns stay a plain string, unchanged from
        # before tool support existed.
        content: str | list[ContentBlock]
        if tool_use_blocks:
            content = blocks
        else:
            content = "".join(block.text for block in blocks if isinstance(block, TextBlock))

        return ProviderResponse(
            message=ChatMessage(role="assistant", content=content),
            stop_reason=response.stop_reason or "unknown",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            tool_calls=[ToolCall(id=b.id, name=b.name, input=b.input) for b in tool_use_blocks],
        )


def create_anthropic_provider(settings: Settings) -> AnthropicProvider:
    """Production constructor — builds the real SDK client from config.
    Tests instead construct AnthropicProvider directly with a fake client.
    Fails fast with the platform's own ProviderAuthError when no API key is
    configured, rather than letting the Anthropic SDK's internal credential
    check raise a raw TypeError the Gateway's error handler doesn't know
    how to map."""
    if not settings.anthropic_api_key:
        raise ProviderAuthError(
            "AI_PLATFORM_ANTHROPIC_API_KEY is not set — the Anthropic SDK has no credentials to call the API with"
        )
    return AnthropicProvider(AsyncAnthropic(api_key=settings.anthropic_api_key))
