from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ai_platform.api.dependencies import get_tracer
from ai_platform.api.middleware.auth import verify_api_key
from ai_platform.common.errors import TraceNotFoundError
from ai_platform.tracing.in_memory import InMemoryTracer
from ai_platform.tracing.types import Span

router = APIRouter()


class TraceResponse(BaseModel):
    trace_id: str
    spans: list[Span]


@router.get("/v1/traces/{trace_id}", response_model=TraceResponse)
async def get_trace(
    trace_id: str,
    tracer: InMemoryTracer = Depends(get_tracer),
    _: str = Depends(verify_api_key),
) -> TraceResponse:
    spans = await tracer.get_trace(trace_id)
    if not spans:
        raise TraceNotFoundError(f"No trace found for id {trace_id}")
    return TraceResponse(trace_id=trace_id, spans=spans)
