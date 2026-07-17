import pytest
from fastapi.testclient import TestClient

from ai_platform.api.app import create_app
from ai_platform.api.dependencies import get_runtime_client, get_tracer
from ai_platform.api.middleware.rate_limit import get_rate_limiter
from ai_platform.common.config import get_settings
from ai_platform.common.schemas import ChatMessage, ChatRequest, ChatResponse


class FakeRuntimeClient:
    """Default RuntimeClient for Gateway tests. Echoes the last user
    message back, so auth/rate-limit/routing tests exercise real Gateway
    wiring without depending on Runtime or making a network call to
    Anthropic — the real RuntimeEngine is tested on its own in
    tests/runtime/."""

    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
        last_user_message = request.messages[-1].content
        return ChatResponse(
            message=ChatMessage(role="assistant", content=f"echo: {last_user_message}"),
            model=request.model,
            trace_id=request.conversation_id or "fake-trace-id",
        )


@pytest.fixture(autouse=True)
def clear_caches():
    """Settings, the rate limiter, and the tracer are process-wide singletons
    (lru_cache). Clear them between tests so one test's rate-limit state,
    env overrides, or recorded spans don't leak into the next."""
    get_settings.cache_clear()
    get_rate_limiter.cache_clear()
    get_tracer.cache_clear()
    yield
    get_settings.cache_clear()
    get_rate_limiter.cache_clear()
    get_tracer.cache_clear()


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_runtime_client] = FakeRuntimeClient
    return TestClient(app)
