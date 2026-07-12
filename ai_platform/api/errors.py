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
    RuntimeUnavailableError,
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
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlatformError)
    async def handle_platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        status_code = _STATUS_CODES.get(type(exc), 500)
        return JSONResponse(
            status_code=status_code,
            content={"error": exc.__class__.__name__, "detail": str(exc)},
        )
