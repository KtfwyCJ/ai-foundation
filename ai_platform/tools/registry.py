from ai_platform.common.errors import ToolNotFoundError
from ai_platform.common.schemas import ToolDefinition
from ai_platform.tools.interfaces import Tool


class ToolRegistry:
    """Holds every tool Runtime is allowed to expose to the model, keyed by
    name. Adding a tool is a registration call, not an edit to Runtime's
    tool-loop logic — the same additive shape as adding a new ModelProvider."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.definition.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"No tool registered under name {name!r}") from None

    def definitions(self) -> list[ToolDefinition]:
        return [tool.definition for tool in self._tools.values()]
