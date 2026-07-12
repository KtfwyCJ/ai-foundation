import httpx
import pytest


class FakeContentBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeAnthropicResponse:
    """Stands in for anthropic.types.Message — only the fields
    AnthropicProvider actually reads."""

    def __init__(
        self,
        text: str = "hello",
        stop_reason: str = "end_turn",
        input_tokens: int = 10,
        output_tokens: int = 5,
    ) -> None:
        self.content = [FakeContentBlock(text)]
        self.stop_reason = stop_reason
        self.usage = FakeUsage(input_tokens, output_tokens)


class FakeMessages:
    """Stands in for AsyncAnthropic().messages — records the last call so
    tests can assert on the translated request shape, and can be told to
    raise instead of returning a response."""

    def __init__(self, response: FakeAnthropicResponse | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class FakeAnthropicClient:
    """Stands in for anthropic.AsyncAnthropic — AnthropicProvider only
    ever touches `.messages.create`, so that's all this fakes."""

    def __init__(self, response: FakeAnthropicResponse | None = None, error: Exception | None = None) -> None:
        self.messages = FakeMessages(response=response, error=error)


def build_status_error(cls, status_code: int = 400):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code, request=request)
    return cls("boom", response=response, body=None)


def build_timeout_error(cls):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(request=request)


@pytest.fixture
def fake_response() -> FakeAnthropicResponse:
    return FakeAnthropicResponse(text="hi there", stop_reason="end_turn", input_tokens=12, output_tokens=8)
