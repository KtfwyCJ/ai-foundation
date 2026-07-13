import anthropic
import pytest

from ai_platform.common.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from ai_platform.common.config import Settings
from ai_platform.common.schemas import ChatMessage, ToolDefinition, ToolResultBlock, ToolUseBlock
from ai_platform.providers.anthropic_provider import AnthropicProvider, create_anthropic_provider

from .conftest import FakeAnthropicClient, FakeAnthropicResponse, FakeToolUseBlock, build_status_error, build_timeout_error


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


async def test_complete_without_system_message_omits_system_key(fake_response):
    client = FakeAnthropicClient(response=fake_response)
    provider = AnthropicProvider(client=client)

    await provider.complete([ChatMessage(role="user", content="hi")], model="claude-sonnet-5")

    # Not `is None` — the key must be absent entirely, since the real SDK
    # treats passing `system=None` as "send JSON null" (which the API
    # rejects), not "omit this parameter."
    assert "system" not in client.messages.last_kwargs


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


async def test_complete_without_tools_omits_tools_kwarg(fake_response):
    client = FakeAnthropicClient(response=fake_response)
    provider = AnthropicProvider(client=client)

    await provider.complete([ChatMessage(role="user", content="hi")], model="claude-sonnet-5")

    assert "tools" not in client.messages.last_kwargs


async def test_complete_translates_tool_definitions_to_anthropic_schema(fake_response):
    client = FakeAnthropicClient(response=fake_response)
    provider = AnthropicProvider(client=client)
    tools = [ToolDefinition(name="calculator", description="does math", input_schema={"type": "object"})]

    await provider.complete([ChatMessage(role="user", content="hi")], model="claude-sonnet-5", tools=tools)

    assert client.messages.last_kwargs["tools"] == [
        {"name": "calculator", "description": "does math", "input_schema": {"type": "object"}}
    ]


async def test_complete_parses_tool_use_block_into_tool_calls():
    tool_use_block = FakeToolUseBlock(id="call_1", name="calculator", input={"operation": "add", "a": 1, "b": 2})
    response = FakeAnthropicResponse(stop_reason="tool_use", content_blocks=[tool_use_block])
    client = FakeAnthropicClient(response=response)
    provider = AnthropicProvider(client=client)

    result = await provider.complete([ChatMessage(role="user", content="what is 1+2?")], model="claude-sonnet-5")

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "calculator"
    assert result.tool_calls[0].input == {"operation": "add", "a": 1, "b": 2}
    assert result.message.content == [ToolUseBlock(id="call_1", name="calculator", input={"operation": "add", "a": 1, "b": 2})]


async def test_complete_sends_tool_result_block_back_to_anthropic():
    client = FakeAnthropicClient(response=FakeAnthropicResponse())
    provider = AnthropicProvider(client=client)
    tool_use = ToolUseBlock(id="call_1", name="calculator", input={"operation": "add", "a": 1, "b": 2})
    tool_result = ToolResultBlock(tool_use_id="call_1", content="3")
    messages = [
        ChatMessage(role="user", content="what is 1+2?"),
        ChatMessage(role="assistant", content=[tool_use]),
        ChatMessage(role="user", content=[tool_result]),
    ]

    await provider.complete(messages, model="claude-sonnet-5")

    assert client.messages.last_kwargs["messages"][1] == {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "call_1", "name": "calculator", "input": {"operation": "add", "a": 1, "b": 2}}],
    }
    assert client.messages.last_kwargs["messages"][2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "3"}],
    }


def test_create_anthropic_provider_raises_provider_auth_error_when_key_missing():
    settings = Settings(anthropic_api_key="")

    with pytest.raises(ProviderAuthError, match="AI_PLATFORM_ANTHROPIC_API_KEY"):
        create_anthropic_provider(settings)


def test_create_anthropic_provider_succeeds_when_key_present():
    settings = Settings(anthropic_api_key="sk-test-key")

    provider = create_anthropic_provider(settings)

    assert isinstance(provider, AnthropicProvider)
