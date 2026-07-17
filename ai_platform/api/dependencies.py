from functools import lru_cache

from ai_platform.common.config import get_settings
from ai_platform.common.interfaces import RuntimeClient
from ai_platform.memory.in_memory import InMemoryStore
from ai_platform.memory.interfaces import MemoryStore
from ai_platform.planning.interfaces import Planner
from ai_platform.planning.llm_planner import LLMPlanner
from ai_platform.providers.anthropic_provider import create_anthropic_provider
from ai_platform.providers.interfaces import ModelProvider
from ai_platform.runtime.engine import RuntimeEngine
from ai_platform.sandbox.interfaces import Sandbox
from ai_platform.sandbox.subprocess_sandbox import SubprocessSandbox
from ai_platform.tools.builtin import CalculatorTool
from ai_platform.tools.registry import ToolRegistry
from ai_platform.tracing.in_memory import InMemoryTracer


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
def get_tracer() -> InMemoryTracer:
    """The one place the concrete Tracer backend is chosen. Swapping
    InMemoryTracer for an OpenTelemetry/Datadog exporter later changes only
    this function. Returns the concrete InMemoryTracer (not the Tracer
    protocol) because the trace-viewer route needs get_trace(), a
    query capability InMemoryTracer has but Tracer deliberately doesn't
    declare — Tracer stays a pure write-only sink so a future export-based
    backend (OpenTelemetry) can still satisfy it."""
    return InMemoryTracer()


@lru_cache
def get_sandbox() -> Sandbox:
    """The one place the concrete Sandbox backend is chosen. Swapping
    SubprocessSandbox for a container-based implementation later changes
    only this function."""
    return SubprocessSandbox()


def get_planner(provider: ModelProvider) -> Planner:
    """The one place the concrete Planner backend is chosen. Takes the
    already-constructed ModelProvider as a parameter — unlike its zero-arg
    @lru_cache siblings — because LLMPlanner reuses the same provider
    instance RuntimeEngine calls for real completions, rather than
    constructing an independent one; caching it separately from
    get_runtime_client (itself cached) would add nothing."""
    return LLMPlanner(provider)


@lru_cache
def get_runtime_client() -> RuntimeClient:
    """The one place that wires a concrete Runtime implementation into the
    Gateway. Swapping the provider, or Runtime itself, changes only this
    function — no route or middleware code depends on the concrete type."""
    provider = create_anthropic_provider(get_settings())
    return RuntimeEngine(
        provider,
        get_tool_registry(),
        get_memory_store(),
        get_tracer(),
        get_sandbox(),
        get_planner(provider),
    )
