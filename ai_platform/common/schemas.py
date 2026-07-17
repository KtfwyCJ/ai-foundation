from typing import Literal, Union

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    """A model's request to call a tool. Carries the vendor-assigned call id
    so the matching ToolResultBlock can reference it back."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict


class ToolResultBlock(BaseModel):
    """A tool's output, addressed back to the ToolUseBlock that requested
    it via tool_use_id."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str


ContentBlock = Union[TextBlock, ToolUseBlock, ToolResultBlock]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    # Plain text for ordinary turns; a list of blocks for a turn that carries
    # tool_use/tool_result data. Kept as a union rather than always-a-list so
    # every existing plain-text message stays exactly as simple as before.
    content: str | list[ContentBlock]


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str = "claude-sonnet-5"
    # Omitted: request is stateless, exactly as before this field existed.
    # Provided: Runtime loads/persists history under this id via MemoryStore,
    # so the caller only has to send this turn's new message(s), not the
    # whole conversation every time.
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    message: ChatMessage
    model: str
    trace_id: str


class ToolDefinition(BaseModel):
    """Provider-agnostic description of a callable tool: name, description,
    and a JSON Schema for its arguments. Lives here, not in tools/, because
    both the Provider layer (translates it into a vendor's tool wire format)
    and the Tool Registry (produces it from a registered Tool) need the
    same shape."""

    name: str
    description: str
    input_schema: dict
