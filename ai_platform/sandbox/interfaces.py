from typing import Any, Protocol

from ai_platform.sandbox.types import SandboxResult
from ai_platform.tools.interfaces import Tool


class Sandbox(Protocol):
    """What Runtime needs to execute a tool call without trusting it. A
    model chooses a tool's arguments, which makes every tool call untrusted
    input by construction — Sandbox is the boundary that enforces a
    wall-clock timeout and a memory ceiling on that execution, the same way
    the Gateway is the boundary that enforces auth and rate limits on a
    request. Runtime treats this as optional (None means "run the tool
    directly, as before"), so adding a Sandbox never changes behavior for
    callers who don't configure one."""

    async def run(self, tool: Tool, kwargs: dict[str, Any]) -> SandboxResult: ...
