from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ai_platform.common.errors import (
    AuthenticationError,
    PlatformError,
    RateLimitExceededError,
    RuntimeUnavailableError,
    ValidationError,
)

_STATUS_CODES: dict[type[PlatformError], int] = {
    AuthenticationError: 401,
    RateLimitExceededError: 429,
    ValidationError: 422,
    RuntimeUnavailableError: 503,
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlatformError)
    async def handle_platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        status_code = _STATUS_CODES.get(type(exc), 500)
        return JSONResponse(
            status_code=status_code,
            content={"error": exc.__class__.__name__, "detail": str(exc)},
        )
