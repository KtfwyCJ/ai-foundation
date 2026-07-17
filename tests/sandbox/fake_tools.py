import asyncio

from ai_platform.common.schemas import ToolDefinition

_DEFINITION = ToolDefinition(name="fake", description="test fixture", input_schema={"type": "object"})


class SlowTool:
    """Never returns within any reasonable timeout — exercises the
    Sandbox's wall-clock enforcement. Defined at module level (not nested
    inside a test function) because the "spawn" multiprocessing start
    method pickles the target by import path, not by value."""

    @property
    def definition(self) -> ToolDefinition:
        return _DEFINITION

    async def execute(self, **kwargs) -> str:
        await asyncio.sleep(60)
        return "unreachable"


class MemoryHogTool:
    """Allocates well past any reasonable per-tool memory ceiling —
    exercises the Sandbox's RLIMIT_AS enforcement."""

    @property
    def definition(self) -> ToolDefinition:
        return _DEFINITION

    async def execute(self, **kwargs) -> str:
        chunks = []
        for _ in range(1024):
            chunks.append(bytearray(10 * 1024 * 1024))  # 10MB per chunk
        return "unreachable"


class EchoTool:
    """Deterministic, fast, no side effects — the well-behaved baseline."""

    @property
    def definition(self) -> ToolDefinition:
        return _DEFINITION

    async def execute(self, *, message: str) -> str:
        return message


class FailingTool:
    """Raises a plain application error — exercises the Sandbox's
    passthrough of ordinary tool failures (not a limit being hit)."""

    @property
    def definition(self) -> ToolDefinition:
        return _DEFINITION

    async def execute(self, **kwargs) -> str:
        raise ValueError("tool blew up")
