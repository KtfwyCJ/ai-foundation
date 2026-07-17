import json

from ai_platform.common.schemas import ChatMessage, ChatRequest, ToolDefinition
from ai_platform.planning.types import Plan, PlanStep
from ai_platform.providers.interfaces import ModelProvider

_PLANNER_PROMPT = (
    "Break the user's request into a short, ordered list of concrete steps "
    "needed to fulfill it. Respond with ONLY a JSON array of objects, each "
    'with a "description" field and an optional "tool_hint" field naming '
    "a tool the step is expected to use. Example: "
    '[{"description": "look up the current weather", "tool_hint": "weather"}]'
)


def _describe_tools(tools: list[ToolDefinition]) -> str:
    if not tools:
        return "No tools are registered — every step's tool_hint should be omitted."
    lines = "\n".join(f'- "{tool.name}": {tool.description}' for tool in tools)
    return f"Available tools (use their exact names as tool_hint when a step needs one):\n{lines}"


def _strip_code_fence(content: str) -> str:
    """Models routinely wrap JSON output in a markdown code fence (```json
    ... ```) even when told to respond with only the JSON itself — strip one
    off if present, so that common, otherwise-correct responses still parse
    instead of silently falling back to an empty Plan."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = stripped.removeprefix("```json").removeprefix("```").strip()
    return stripped.removesuffix("```").strip()


def _parse_plan(content: str | list) -> Plan:
    if not isinstance(content, str):
        return Plan()
    try:
        raw_steps = json.loads(_strip_code_fence(content))
    except json.JSONDecodeError:
        return Plan()
    if not isinstance(raw_steps, list):
        return Plan()

    steps = [
        PlanStep(description=raw["description"], tool_hint=raw.get("tool_hint"))
        for raw in raw_steps
        if isinstance(raw, dict) and isinstance(raw.get("description"), str)
    ]
    return Plan(steps=steps)


class LLMPlanner:
    """Planner implementation that asks the model itself — via the same
    ModelProvider RuntimeEngine already depends on — to decompose a request
    into an ordered Plan, in a dedicated completion separate from the
    execution loop's own calls. A malformed or non-JSON response yields an
    empty Plan rather than raising: v0.1 is observational only, so a
    planning hiccup must never be allowed to affect the real chat request
    RuntimeEngine still has to answer."""

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    async def plan(self, request: ChatRequest, tools: list[ToolDefinition]) -> Plan:
        planning_messages: list[ChatMessage] = [
            ChatMessage(role="system", content=f"{_PLANNER_PROMPT}\n\n{_describe_tools(tools)}"),
            *request.messages,
        ]
        # Deliberately not passing tools=tools through to complete(): that
        # would let the model actually invoke a tool (a tool_use block)
        # instead of returning the JSON plan text this parses. Tool names
        # are grounded via the prompt above instead, so tool_hint values
        # can still reference real tools without risking a live tool call.
        result = await self._provider.complete(planning_messages, model=request.model)
        return _parse_plan(result.message.content)
