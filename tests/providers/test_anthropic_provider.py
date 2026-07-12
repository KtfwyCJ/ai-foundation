import anthropic
import pytest

from ai_platform.common.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from ai_platform.common.schemas import ChatMessage
from ai_platform.providers.anthropic_provider import AnthropicProvider

from .conftest import FakeAnthropicClient, build_status_error, build_timeout_error


async def test_complete_extracts_system_message_separately(fake_response):
    client = FakeAnthropicClient(response=fake_response)
    provider = AnthropicProvider(client=client)
    messages = [
        ChatMessage(role="system", content="be terse"),
        ChatMessage(role="user", content="hi"),
    ]

    await provider.complete(messages, model="claude-sonnet-5")

    assert client.messages.last_kwargs["system"] == "be terse"
    assert client.messages.last_kwargs["messages"] == [{"role": "user", "content": "hi"}]


async def test_complete_without_system_message_passes_none(fake_response):
    client = FakeAnthropicClient(response=fake_response)
    provider = AnthropicProvider(client=client)

    await provider.complete([ChatMessage(role="user", content="hi")], model="claude-sonnet-5")

    assert client.messages.last_kwargs["system"] is None


async def test_complete_parses_response_into_provider_response(fake_response):
    client = FakeAnthropicClient(response=fake_response)
    provider = AnthropicProvider(client=client)

    result = await provider.complete([ChatMessage(role="user", content="hi")], model="claude-sonnet-5")

    assert result.message.role == "assistant"
    assert result.message.content == "hi there"
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 12
    assert result.output_tokens == 8


async def test_authentication_error_is_translated():
    error = build_status_error(anthropic.AuthenticationError, status_code=401)
    client = FakeAnthropicClient(error=error)
    provider = AnthropicProvider(client=client)

    with pytest.raises(ProviderAuthError):
        await provider.complete([ChatMessage(role="user", content="hi")], model="claude-sonnet-5")


async def test_rate_limit_error_is_translated():
    error = build_status_error(anthropic.RateLimitError, status_code=429)
    client = FakeAnthropicClient(error=error)
    provider = AnthropicProvider(client=client)

    with pytest.raises(ProviderRateLimitError):
        await provider.complete([ChatMessage(role="user", content="hi")], model="claude-sonnet-5")


async def test_timeout_error_is_translated():
    error = build_timeout_error(anthropic.APITimeoutError)
    client = FakeAnthropicClient(error=error)
    provider = AnthropicProvider(client=client)

    with pytest.raises(ProviderTimeoutError):
        await provider.complete([ChatMessage(role="user", content="hi")], model="claude-sonnet-5")


async def test_generic_status_error_is_translated():
    error = build_status_error(anthropic.APIStatusError, status_code=500)
    client = FakeAnthropicClient(error=error)
    provider = AnthropicProvider(client=client)

    with pytest.raises(ProviderError):
        await provider.complete([ChatMessage(role="user", content="hi")], model="claude-sonnet-5")
