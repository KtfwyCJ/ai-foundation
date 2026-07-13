import pytest

from ai_platform.tools.builtin import CalculatorTool


@pytest.mark.parametrize(
    ("operation", "a", "b", "expected"),
    [
        ("add", 2, 3, "5"),
        ("subtract", 5, 3, "2"),
        ("multiply", 4, 3, "12"),
        ("divide", 10, 2, "5.0"),
    ],
)
async def test_execute_performs_the_requested_operation(operation, a, b, expected):
    tool = CalculatorTool()

    result = await tool.execute(operation=operation, a=a, b=b)

    assert result == expected


async def test_execute_raises_on_unknown_operation():
    tool = CalculatorTool()

    with pytest.raises(ValueError):
        await tool.execute(operation="modulo", a=1, b=1)


def test_definition_declares_required_arguments():
    tool = CalculatorTool()

    definition = tool.definition

    assert definition.name == "calculator"
    assert definition.input_schema["required"] == ["operation", "a", "b"]
