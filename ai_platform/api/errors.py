from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ai_platform.common.errors import (
    AuthenticationError,
    PlatformError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    RateLimitExceededError,
    RuntimeToolLoopExceededError,
    RuntimeUnavailableError,
    SandboxError,
    SandboxResourceLimitError,
    SandboxTimeoutError,
    ToolNotFoundError,
    TraceNotFoundError,
    ValidationError,
)

_STATUS_CODES: dict[type[PlatformError], int] = {
    AuthenticationError: 401,
    RateLimitExceededError: 429,
    ValidationError: 422,
    RuntimeUnavailableError: 503,
    # Provider errors aren't reachable through the Gateway yet (Runtime,
    # which will call providers, doesn't exist yet) but are mapped now so
    # nothing needs to change here once Runtime starts raising them.
    ProviderAuthError: 500,  # our credentials are misconfigured, not the caller's fault
    ProviderRateLimitError: 503,  # the provider itself is throttling us
    ProviderTimeoutError: 504,
    ProviderError: 502,
    ToolNotFoundError: 500,  # a tool we exposed to the model isn't actually registered
    RuntimeToolLoopExceededError: 503,  # model never converged on a final answer
    SandboxTimeoutError: 504,
    SandboxResourceLimitError: 500,  # tool's own limit was misconfigured, not the caller's fault
    SandboxError: 500,
    TraceNotFoundError: 404,
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlatformError)
    async def handle_platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        status_code = _STATUS_CODES.get(type(exc), 500)
        return JSONResponse(
            status_code=status_code,
            content={"error": exc.__class__.__name__, "detail": str(exc)},
        )
