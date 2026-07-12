import time
from functools import lru_cache

from fastapi import Depends

from ai_platform.api.middleware.auth import verify_api_key
from ai_platform.common.config import get_settings
from ai_platform.common.errors import RateLimitExceededError



class RateLimiter:
    """Fixed-window in-memory rate limiter, keyed per API key.

    Trade-off: in-memory state is per-process — limits reset on restart and
    don't share state across multiple Gateway replicas. A production
    multi-replica deployment would back this with Redis (INCR + EXPIRE)
    instead. In-memory is sufficient for a single-instance deployment.
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._windows: dict[str, tuple[float, int]] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        window_start, count = self._windows.get(key, (now, 0))

        if now - window_start >= self._window_seconds:
            window_start, count = now, 0

        count += 1
        self._windows[key] = (window_start, count)

        if count > self._max_requests:
            raise RateLimitExceededError("Rate limit exceeded")


@lru_cache
def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return RateLimiter(
        max_requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


def enforce_rate_limit(
    api_key: str = Depends(verify_api_key),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:
    limiter.check(api_key)
