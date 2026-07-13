from typing import Any, Protocol

from ai_platform.common.schemas import ToolDefinition


class Tool(Protocol):
    """What the Tool Registry needs from a tool. Runtime never calls a tool
    directly — it goes through the registry, which is what lets a new tool
    be added by registration rather than by editing Runtime's tool-loop."""

    @property
    def definition(self) -> ToolDefinition: ...

    async def execute(self, **kwargs: Any) -> str: ...
