from pydantic import BaseModel, Field

from ai_platform.common.schemas import ChatMessage


class ToolCall(BaseModel):
    """A single tool invocation the model requested, parsed out of a
    ToolUseBlock. Kept as its own type (rather than making callers dig
    through ProviderResponse.message.content) since checking "did the model
    ask for a tool" is Runtime's main branch point after every completion."""

    id: str
    name: str
    input: dict


class ProviderResponse(BaseModel):
    """A model provider's response translated into a platform-generic
    shape. Carries usage/stop_reason because cost tracking and finish-reason
    handling are provider-level concerns — the Gateway's ChatResponse
    doesn't need to expose them today, but Runtime will need them
    internally (e.g. for audit logging and cost accounting)."""

    message: ChatMessage
    stop_reason: str
    input_tokens: int
    output_tokens: int
    tool_calls: list[ToolCall] = Field(default_factory=list)
