from pydantic import BaseModel

from ai_platform.common.schemas import ChatMessage


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
