from ai_platform.common.schemas import ToolDefinition


class CalculatorTool:
    """Example Tool implementation. Exists to exercise the tool-calling loop
    end-to-end with something deterministic and easy to test — not meant to
    be the platform's real tool catalog."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="calculator",
            description="Perform a basic arithmetic operation on two numbers.",
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                    },
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["operation", "a", "b"],
            },
        )

    async def execute(self, *, operation: str, a: float, b: float) -> str:
        if operation == "add":
            result = a + b
        elif operation == "subtract":
            result = a - b
        elif operation == "multiply":
            result = a * b
        elif operation == "divide":
            result = a / b
        else:
            raise ValueError(f"Unknown operation: {operation!r}")
        return str(result)
