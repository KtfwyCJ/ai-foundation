import sys

import pytest

from ai_platform.common.errors import SandboxResourceLimitError, SandboxTimeoutError
from ai_platform.sandbox.subprocess_sandbox import SubprocessSandbox
from ai_platform.sandbox.types import SandboxLimits

from .fake_tools import EchoTool, FailingTool, MemoryHogTool, SlowTool

_LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="RLIMIT_AS is not enforced by the kernel on macOS/Darwin; the memory ceiling only "
    "has real effect on Linux (the project's actual deployment target)",
)


async def test_run_returns_tool_output_on_success():
    sandbox = SubprocessSandbox(SandboxLimits(timeout_s=5.0, max_memory_mb=256))

    result = await sandbox.run(EchoTool(), {"message": "hello"})

    assert result.output == "hello"
    assert result.duration_ms >= 0


async def test_run_raises_sandbox_timeout_error_when_tool_exceeds_timeout():
    sandbox = SubprocessSandbox(SandboxLimits(timeout_s=0.3, max_memory_mb=256))

    with pytest.raises(SandboxTimeoutError):
        await sandbox.run(SlowTool(), {})


@_LINUX_ONLY
async def test_run_raises_sandbox_resource_limit_error_when_tool_exceeds_memory():
    sandbox = SubprocessSandbox(SandboxLimits(timeout_s=10.0, max_memory_mb=20))

    with pytest.raises(SandboxResourceLimitError):
        await sandbox.run(MemoryHogTool(), {})


async def test_run_lets_ordinary_tool_errors_propagate():
    sandbox = SubprocessSandbox(SandboxLimits(timeout_s=5.0, max_memory_mb=256))

    with pytest.raises(RuntimeError, match="tool blew up"):
        await sandbox.run(FailingTool(), {})
