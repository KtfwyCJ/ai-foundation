from typing import Protocol

from ai_platform.common.schemas import ChatRequest, ToolDefinition
from ai_platform.planning.types import Plan


class Planner(Protocol):
    """What Runtime needs to get an upfront plan for a request, before its
    reactive tool-calling loop runs. v0.1 is observational only:
    RuntimeEngine records the resulting Plan as a Span but does not change
    execution based on it — see the Sandbox/Tracing tutorials for the same
    "ship visibility before enforcement" precedent this module follows."""

    async def plan(self, request: ChatRequest, tools: list[ToolDefinition]) -> Plan: ...
