import pytest

from ai_platform.common.errors import ToolNotFoundError
from ai_platform.tools.builtin import CalculatorTool
from ai_platform.tools.registry import ToolRegistry


def test_register_and_get_returns_the_same_tool():
    registry = ToolRegistry()
    tool = CalculatorTool()

    registry.register(tool)

    assert registry.get("calculator") is tool


def test_get_unknown_tool_raises_tool_not_found_error():
    registry = ToolRegistry()

    with pytest.raises(ToolNotFoundError):
        registry.get("does_not_exist")


def test_definitions_returns_one_definition_per_registered_tool():
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    definitions = registry.definitions()

    assert [d.name for d in definitions] == ["calculator"]


def test_definitions_is_empty_for_a_fresh_registry():
    registry = ToolRegistry()

    assert registry.definitions() == []
