import asyncio
import multiprocessing
import resource
import time
from multiprocessing.connection import Connection
from typing import Any

from ai_platform.common.errors import SandboxResourceLimitError, SandboxTimeoutError
from ai_platform.sandbox.types import SandboxLimits, SandboxResult
from ai_platform.tools.interfaces import Tool


def _apply_memory_limit(max_memory_mb: int) -> None:
    """RLIMIT_AS exists on macOS/Darwin but the kernel doesn't actually
    enforce a lowered value — setrlimit raises there regardless of the
    requested limit, on every call, not just ones that would exceed it. A
    platform quirk in one enforcement mechanism shouldn't break every
    sandboxed call, so this degrades to "no memory ceiling" rather than
    propagating the error — enforced normally on Linux, which is what the
    project actually ships on (Docker image, ubuntu-latest CI)."""
    try:
        max_bytes = max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
    except (ValueError, OSError):
        pass


def _run_tool_in_child(tool: Tool, kwargs: dict[str, Any], max_memory_mb: int, conn: Connection) -> None:
    """Entry point for the child process. Applies the memory ceiling before
    touching the tool at all, then runs its (async) execute() to completion
    with asyncio.run() — the child has nothing else to do, so it doesn't
    need its own event loop lifecycle beyond this one call."""
    try:
        _apply_memory_limit(max_memory_mb)
        output = asyncio.run(tool.execute(**kwargs))
        conn.send(("ok", output))
    except MemoryError:
        conn.send(("memory", None))
    except Exception as exc:
        conn.send(("error", f"{exc.__class__.__name__}: {exc}"))
    finally:
        conn.close()


class SubprocessSandbox:
    """v0.1 Sandbox: runs each tool call in a fresh child process (spawned
    fresh per call, not pooled — the simplest thing that gives every call
    its own clean memory ceiling and lets a runaway call be killed without
    touching any other call) with an RLIMIT_AS memory ceiling applied before
    the tool ever runs, and a wall-clock timeout enforced by the parent.
    Isolates CPU, memory, and time from the host process — on Linux; on
    macOS/Darwin the kernel doesn't enforce a lowered RLIMIT_AS, so the
    memory ceiling silently has no effect there and only the timeout is
    real (see _apply_memory_limit). Does not isolate filesystem or network
    access on any platform — a tool can still read/write the host
    filesystem or make network calls; a container-based Sandbox can be
    added later behind the same Protocol for that stronger guarantee."""

    def __init__(self, limits: SandboxLimits | None = None) -> None:
        self._limits = limits or SandboxLimits()

    async def run(self, tool: Tool, kwargs: dict[str, Any]) -> SandboxResult:
        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe()
        process = ctx.Process(
            target=_run_tool_in_child,
            args=(tool, kwargs, self._limits.max_memory_mb, child_conn),
        )
        start = time.monotonic()
        process.start()
        child_conn.close()  # only the child should hold the writable end

        try:
            status, payload = await asyncio.wait_for(
                self._receive(parent_conn), timeout=self._limits.timeout_s
            )
        except asyncio.TimeoutError:
            process.kill()
            await asyncio.to_thread(process.join, 2)
            raise SandboxTimeoutError(
                f"Tool {tool.definition.name!r} exceeded {self._limits.timeout_s}s timeout"
            ) from None
        except EOFError:
            # Child died without sending a result — most likely the OS
            # killed it outright for exceeding the memory ceiling before
            # CPython could raise and catch a clean MemoryError.
            process.kill()
            await asyncio.to_thread(process.join, 2)
            raise SandboxResourceLimitError(
                f"Tool {tool.definition.name!r} process exited unexpectedly, likely for exceeding "
                f"the {self._limits.max_memory_mb}MB memory limit"
            ) from None
        else:
            await asyncio.to_thread(process.join, 2)
        finally:
            parent_conn.close()

        if status == "memory":
            raise SandboxResourceLimitError(
                f"Tool {tool.definition.name!r} exceeded {self._limits.max_memory_mb}MB memory limit"
            )
        if status == "error":
            raise RuntimeError(payload)
        return SandboxResult(output=payload, duration_ms=(time.monotonic() - start) * 1000)

    async def _receive(self, conn: Connection) -> tuple[str, Any]:
        return await asyncio.to_thread(conn.recv)
