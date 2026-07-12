import pytest
from fastapi.testclient import TestClient

from ai_platform.api.app import create_app
from ai_platform.api.middleware.rate_limit import get_rate_limiter
from ai_platform.common.config import get_settings


@pytest.fixture(autouse=True)
def clear_caches():
    """Settings and the rate limiter are process-wide singletons (lru_cache).
    Clear them between tests so one test's rate-limit state or env overrides
    don't leak into the next."""
    get_settings.cache_clear()
    get_rate_limiter.cache_clear()
    yield
    get_settings.cache_clear()
    get_rate_limiter.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
