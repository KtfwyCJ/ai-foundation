import pytest

from ai_platform.common.schemas import ChatMessage, ToolDefinition
from ai_platform.providers.types import ProviderResponse


class FakeModelProvider:
    """Stands in for a ModelProvider — AnthropicProvider or otherwise.
    Records every call so tests can assert on what RuntimeEngine sent on
    each iteration of the tool loop, and can be told to raise instead of
    returning a response. Accepts either a single `response` (returned every
    call) or a `responses` sequence (one per call, in order — the last one
    repeats if the loop calls more times than the sequence has entries)."""

    def __init__(
        self,
        response: ProviderResponse | None = None,
        responses: list[ProviderResponse] | None = None,
        error: Exception | None = None,
    ) -> None:
        if responses is not None:
            self._responses = list(responses)
        elif response is not None:
            self._responses = [response]
        else:
            self._responses = []
        self._error = error
        self.calls: list[dict] = []

    @property
    def last_messages(self) -> list[ChatMessage] | None:
        return self.calls[-1]["messages"] if self.calls else None

    @property
    def last_model(self) -> str | None:
        return self.calls[-1]["model"] if self.calls else None

    @property
    def last_tools(self) -> list[ToolDefinition] | None:
        return self.calls[-1]["tools"] if self.calls else None

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int = 1024,
        tools: list[ToolDefinition] | None = None,
    ) -> ProviderResponse:
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        if self._error is not None:
            raise self._error
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


@pytest.fixture
def fake_provider_response() -> ProviderResponse:
    return ProviderResponse(
        message=ChatMessage(role="assistant", content="hi there"),
        stop_reason="end_turn",
        input_tokens=12,
        output_tokens=8,
    )
