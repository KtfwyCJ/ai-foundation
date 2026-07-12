from fastapi import APIRouter, Depends

from ai_platform.api.dependencies import get_runtime_client
from ai_platform.api.middleware.rate_limit import enforce_rate_limit
from ai_platform.common.interfaces import RuntimeClient
from ai_platform.common.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/v1/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    runtime: RuntimeClient = Depends(get_runtime_client),
    _: None = Depends(enforce_rate_limit),
) -> ChatResponse:
    return await runtime.handle_chat(request)
