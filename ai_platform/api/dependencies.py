from functools import lru_cache

from ai_platform.common.config import get_settings
from ai_platform.common.interfaces import RuntimeClient
from ai_platform.memory.in_memory import InMemoryStore
from ai_platform.memory.interfaces import MemoryStore
from ai_platform.providers.anthropic_provider import create_anthropic_provider
from ai_platform.runtime.engine import RuntimeEngine
from ai_platform.sandbox.interfaces import Sandbox
from ai_platform.sandbox.subprocess_sandbox import SubprocessSandbox
from ai_platform.tools.builtin import CalculatorTool
from ai_platform.tools.registry import ToolRegistry
from ai_platform.tracing.in_memory import InMemoryTracer
from ai_platform.tracing.interfaces import Tracer


@lru_cache
def get_tool_registry() -> ToolRegistry:
    """The one place tools get registered. Adding a tool means adding one
    line here, not touching Runtime's tool-loop logic."""
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


@lru_cache
def get_memory_store() -> MemoryStore:
    """The one place the concrete MemoryStore backend is chosen. Swapping
    InMemoryStore for a Redis/DB-backed store later changes only this
    function."""
    return InMemoryStore()


@lru_cache
def get_tracer() -> Tracer:
    """The one place the concrete Tracer backend is chosen. Swapping
    InMemoryTracer for an OpenTelemetry/Datadog exporter later changes only
    this function."""
    return InMemoryTracer()


@lru_cache
def get_sandbox() -> Sandbox:
    """The one place the concrete Sandbox backend is chosen. Swapping
    SubprocessSandbox for a container-based implementation later changes
    only this function."""
    return SubprocessSandbox()


@lru_cache
def get_runtime_client() -> RuntimeClient:
    """The one place that wires a concrete Runtime implementation into the
    Gateway. Swapping the provider, or Runtime itself, changes only this
    function — no route or middleware code depends on the concrete type."""
    provider = create_anthropic_provider(get_settings())
    return RuntimeEngine(
        provider, get_tool_registry(), get_memory_store(), get_tracer(), get_sandbox()
    )
