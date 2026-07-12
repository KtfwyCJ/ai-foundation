from functools import lru_cache

from ai_platform.common.interfaces import RuntimeClient
from ai_platform.runtime.stub import EchoRuntimeClient


@lru_cache
def get_runtime_client() -> RuntimeClient:
    """The one place that wires a concrete Runtime implementation into the
    Gateway. When the real Runtime module exists, only this function changes
    — no route or middleware code depends on the concrete type."""
    return EchoRuntimeClient()
