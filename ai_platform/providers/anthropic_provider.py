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
from ai_platform.common.schemas import ChatMessage
from ai_platform.providers.types import ProviderResponse


class AnthropicProvider:
    """ModelProvider implementation backed by the Anthropic SDK. Owns every
    Claude-specific translation: pulling system messages out of the generic
    message list (Anthropic takes `system` as a separate parameter, not a
    message with role="system"), and mapping SDK exceptions onto the
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
    ) -> ProviderResponse:
        system_prompt = "\n".join(m.content for m in messages if m.role == "system")
        turns = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]

        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt or None,
                messages=turns,
            )
        except AnthropicAuthenticationError as exc:
            raise ProviderAuthError("Anthropic rejected the API key") from exc
        except AnthropicRateLimitError as exc:
            raise ProviderRateLimitError("Anthropic rate limit exceeded") from exc
        except APITimeoutError as exc:
            raise ProviderTimeoutError("Anthropic did not respond in time") from exc
        except APIStatusError as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc

        text = "".join(block.text for block in response.content if block.type == "text")

        return ProviderResponse(
            message=ChatMessage(role="assistant", content=text),
            stop_reason=response.stop_reason or "unknown",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def create_anthropic_provider(settings: Settings) -> AnthropicProvider:
    """Production constructor — builds the real SDK client from config.
    Tests instead construct AnthropicProvider directly with a fake client."""
    return AnthropicProvider(AsyncAnthropic(api_key=settings.anthropic_api_key))
