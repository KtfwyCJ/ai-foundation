from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxLimits:
    """Ceilings a Sandbox enforces on a single tool execution. Defaults are
    generous enough for real tool work (a few seconds, a few hundred MB) but
    still bounded — an unbounded default would defeat the point of having
    limits at all."""

    timeout_s: float = 5.0
    max_memory_mb: int = 256



@dataclass(frozen=True)
class SandboxResult:
    """What a Sandbox returns for a successful run — the tool's output plus
    how long it actually took, so callers get the same observability they'd
    get calling tool.execute() directly."""

    output: str
    duration_ms: float
