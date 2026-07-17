# 10 — The Sandbox Module

*Internal onboarding doc — AI Platform, Sandbox component (`ai_platform/sandbox/`)*

## 1. Executive Summary

The Sandbox module is what stands between a model's tool-call arguments and the host process actually running them. Since module 04, `RuntimeEngine` has executed tools by calling `tool.execute(**call.input)` directly, in-process, with no timeout and no resource ceiling — fine for `CalculatorTool`, a pure, side-effect-free example tool, but a real gap the moment a tool touches a file, a subprocess, or the network.

`Sandbox` is a `Protocol` with one method — `run(tool, kwargs) -> SandboxResult` — and `SubprocessSandbox` is its v0.1 implementation: every tool call runs in a fresh child process, under an enforced wall-clock timeout and (on Linux) a memory ceiling, before its output is handed back to Runtime. `RuntimeEngine` takes an optional `sandbox: Sandbox | None` constructor argument; when one is configured, `_run_tool_calls` routes execution through it instead of calling `tool.execute` directly. No sandbox configured means unchanged v0.1-through-v0.9 behavior — this module is additive, not a rewrite of the tool-calling loop module 04 already built.

This is also the module where a real platform-vs-laptop gap surfaced during implementation, not just in theory: the memory ceiling this module enforces on Linux (the project's actual deployment target) is silently unenforceable on macOS/Darwin, the machine this was built on. That gap, and how the code handles it honestly rather than pretending it doesn't exist, is a running theme through this tutorial.

## 2. The Problem

A tool call's arguments are chosen by the model, not by the caller. That makes every tool call untrusted input by construction, in exactly the sense request bodies are untrusted input at the Gateway — except nothing in the platform enforced that boundary for tool execution the way the Gateway enforces auth and rate limits for requests. Concretely, without a Sandbox:

- **A slow or hanging tool call blocks the entire request indefinitely.** `RuntimeEngine.handle_chat` has no timeout around `tool.execute(...)` — a tool that never returns (a bad network call, an infinite loop triggered by adversarial input) means the request never returns either.
- **A tool call has no resource ceiling.** A tool that allocates unbounded memory or spawns unbounded work runs with the same resources as the Gateway process itself — there's no isolation between "this one tool call misbehaved" and "the whole platform process is now in trouble."
- **There is no place to add these guarantees without either editing every `Tool` implementation, or editing `RuntimeEngine`'s orchestration logic for something orthogonal to orchestration.** Module 04 explicitly deferred this ("Sandbox tool execution in a subprocess/container from day one" — rejected then because the only tool was a trusted calculator). This module is what that deferral becomes once there's a real design to build.

## 3. Motivation

Treating "the model chose these arguments" as a trust boundary is standard practice the moment an agent framework's tools do anything beyond pure computation — it's the same reasoning OWASP's LLM Top 10 calls "Excessive Agency," a risk module 04's tutorial already flagged as this platform's first genuine security surface. Enterprise systems that let an LLM trigger real actions (run code, call internal APIs, touch a filesystem) universally introduce an execution boundary between "the model requested this" and "this actually ran with the host's full privileges" — the names vary (sandbox, worker, isolated executor) but the shape is the same: bound the blast radius of an untrusted call before its result becomes trusted-again by re-entering the conversation.

Introducing that boundary as its own module — rather than inside `RuntimeEngine` or inside each `Tool` — keeps the same separation of concerns this platform has used every time: `ToolRegistry` doesn't know *when* to call a tool (that's Runtime's job); `Tool` implementations don't need to know they're being resource-limited (that's Sandbox's job); and `RuntimeEngine` doesn't need to know *how* isolation is achieved, only that an optional collaborator can provide it.

## 4. Responsibilities

**Sandbox should:**
- Define the `Sandbox` protocol (`interfaces.py`): `async def run(self, tool: Tool, kwargs: dict) -> SandboxResult`
- Enforce a wall-clock timeout on a single tool execution (`SubprocessSandbox`, using `asyncio.wait_for` around the child process's result)
- Enforce a memory ceiling on a single tool execution where the platform (Linux) actually allows it (`_apply_memory_limit`, via `RLIMIT_AS`)
- Translate a limit being hit into the platform's typed error hierarchy (`SandboxTimeoutError`, `SandboxResourceLimitError`) so the Gateway's existing error-mapping picks them up without new Gateway code
- Let an ordinary tool failure (a bug in the tool, not a limit being exceeded) propagate distinguishably from a limit violation

**Sandbox should NOT:**
- Decide *whether* a given tool call needs sandboxing — that's a Runtime/composition-root decision (today: all-or-nothing, whether `RuntimeEngine` was constructed with a `sandbox` or not); a future per-tool policy is a Production Evolution item, not built now
- Isolate the filesystem or network — `SubprocessSandbox`'s child process still has the same filesystem access and network reachability as the host; it only bounds CPU, wall-time, and (on Linux) memory
- Know anything about `ToolRegistry`, `ToolCall`, or the tool-calling loop — it receives a `Tool` and a `kwargs` dict and returns a result; everything about *which* tool and *when* stays in `RuntimeEngine`
- Retry a failed or timed-out call — that decision belongs to whatever calls `Sandbox.run`, same as the platform never retries a `ProviderError` inside the Provider layer itself

## 5. Architecture

```
                 RuntimeEngine
                    │  optional collaborator, same pattern as Tracer/MemoryStore
                    │
                    │  sandbox.run(tool, call.input)  →  SandboxResult
                    ▼
      ┌─────────────────────────────┐
      │   Sandbox (Protocol)           │   ai_platform/sandbox/interfaces.py
      └──────────────┬────────────────┘
                      │ implemented by
                      ▼
      ┌─────────────────────────────┐
      │   SubprocessSandbox            │   ai_platform/sandbox/subprocess_sandbox.py
      │                                 │
      │   parent: enforces timeout,     │
      │     receives result over Pipe   │
      │   child (fresh process):        │
      │     applies RLIMIT_AS, then     │
      │     runs tool.execute(**kwargs) │
      └──────────────┬────────────────┘
                      │ executes, unmodified
                      ▼
      ┌─────────────────────────────┐
      │   Tool (Protocol)              │   ai_platform/tools/interfaces.py
      │   e.g. CalculatorTool           │   (unaware it's being sandboxed)
      └─────────────────────────────┘
```

Upstream: `RuntimeEngine` holds an optional `Sandbox` reference (constructor-injected, same DI pattern as `Tracer`/`MemoryStore`), and calls `sandbox.run(tool, call.input)` from inside `_run_tool_calls` — the exact call site that previously called `tool.execute(**call.input)` directly. Downstream: `SubprocessSandbox` calls `tool.execute(**kwargs)` unmodified, inside the child process — a `Tool` implementation has no idea it's running sandboxed rather than in-process, the same "boundary shouldn't leak into the thing it's protecting" property `ToolRegistry` already has with respect to `RuntimeEngine`'s loop.

Sideways: `common/errors.py` gained a new small hierarchy (`SandboxError` → `SandboxTimeoutError`, `SandboxResourceLimitError`), and `api/errors.py`'s `_STATUS_CODES` gained three entries — the same "add a mapping, touch nothing else" pattern every prior `PlatformError` subclass has used since the Gateway tutorial.

## 6. Request Flow

Walking through a sandboxed tool call (continuing from where the Tool Registry tutorial's request flow left off, now with a `Sandbox` configured):

1. **`RuntimeEngine._run_tool_calls`** looks up the requested tool via `self._tools.get(call.name)`, exactly as before.
2. **Because `self._sandbox` is set**, instead of `await tool.execute(**call.input)`, it calls `await self._sandbox.run(tool, call.input)`.
3. **`SubprocessSandbox.run`** opens a `multiprocessing.Pipe`, and spawns a *fresh* child process (via the `"spawn"` start method) whose target is `_run_tool_in_child`, passing it the tool, the kwargs, the configured memory ceiling, and the pipe's write end.
4. **Inside the child**, `_apply_memory_limit` calls `resource.setrlimit(RLIMIT_AS, (max_bytes, max_bytes))` before the tool ever runs — the ceiling is in place *before* untrusted code executes, not applied reactively after the fact.
5. **The child runs `asyncio.run(tool.execute(**kwargs))`** — the tool's own logic is completely unaware anything unusual is happening — and sends `("ok", output)` back over the pipe.
6. **The parent's `asyncio.wait_for(self._receive(parent_conn), timeout=...)`** was racing against that pipe read the whole time. Three outcomes:
   - **The child responds in time** → parent receives `("ok", output)`, joins the process, and returns a `SandboxResult(output, duration_ms)`.
   - **The child never responds within `timeout_s`** → `asyncio.TimeoutError` fires, the parent kills the process and raises `SandboxTimeoutError`.
   - **The child dies without responding** (e.g., the OS itself kills it for exceeding memory, on platforms where that happens at the OS level rather than as a catchable Python exception) → the pipe read raises `EOFError`, and the parent raises `SandboxResourceLimitError`.
7. **If the child's own `tool.execute` raised** an ordinary exception (not `MemoryError`), the child sends `("error", "<type>: <message>")`, and the parent re-raises it as a `RuntimeError` carrying that description — an ordinary tool bug, not a limit violation, propagates distinguishably from steps 6's limit-violation paths.
8. **Back in `RuntimeEngine._run_tool_calls`**, the result's `.output` is used exactly as `tool.execute`'s return value was before — the rest of the tool-calling loop (recording the span, building the `ToolResultBlock`, continuing the loop) is completely unchanged from module 04.
9. **If no `Sandbox` was configured at all**, none of the above happens — `_run_tool_calls` falls back to calling `tool.execute(**call.input)` directly, byte-for-byte the same code path that existed before this module.

## 7. Design Decisions

**Why a fresh child process per call, instead of a pooled worker?**
A pooled worker means every call after the first shares that worker's address space — a memory ceiling set once at pool creation can't be tightened or loosened per call, and a call that corrupts the worker's state (or simply leaves garbage allocated) contaminates every call after it. Spawning fresh per call costs process-startup overhead on every tool execution, but it means each call gets a clean address space and its own ceiling, and killing a runaway call can never affect any other call — the "simplest thing that's actually correct" trade-off this platform has made before (e.g., the Provider layer staying single-vendor until there's evidence for more).

**Why does the memory ceiling get applied inside the child, not the parent?**
The parent process is the Gateway/Runtime process itself — lowering *its* `RLIMIT_AS` would cap the whole platform's memory, not just one tool call. The ceiling has to apply to the process that's actually about to run untrusted logic, which is the child, and it has to be applied *before* `tool.execute` runs, not wrapped around it afterward — a limit set after the fact can't stop an allocation that already happened.

**Why does the Sandbox boundary live in `RuntimeEngine`, wrapping the `tool.execute` call site, rather than inside each `Tool`?**
The identical reasoning tracing spans already established in module 06: this is the one place request-level execution is observed and controlled, and individual `Tool` implementations shouldn't need to know they're being sandboxed any more than they need to know they're being traced. `CalculatorTool` — still the platform's only real tool, still pure and side-effect-free — pays zero sandboxing cost unless a `Sandbox` is actually configured, because the decision to sandbox is made once, at composition time, not per-tool.

**Why is the memory ceiling silently unenforceable on macOS/Darwin, and why doesn't the code just fail loudly there?**
This was discovered empirically, not assumed: running the test suite locally (a Darwin machine) showed `resource.setrlimit(RLIMIT_AS, ...)` raising `ValueError: current limit exceeds maximum limit` on *every* call, regardless of the requested limit — `RLIMIT_AS` exists on macOS and `getrlimit` reports a value, but the kernel doesn't actually let you lower it via `setrlimit`. Making every sandboxed call fail outright because of a platform quirk in one of two enforcement mechanisms would be strictly worse than the module's entire purpose (bounding untrusted execution) — so `_apply_memory_limit` catches `(ValueError, OSError)` and degrades to "no memory ceiling on this platform" rather than raising. The timeout enforcement, which doesn't depend on `setrlimit` at all, is unaffected and works identically on both platforms. This is documented in the code and in this tutorial rather than hidden, because a Sandbox that silently doesn't sandbox on the developer's own machine is exactly the kind of gap that should be visible, not discovered later in an incident.

**Why `RuntimeError` for ordinary tool failures instead of re-raising the tool's original exception type?**
The child process's exception can't be pickled back across the process boundary in general (many exception types, especially ones carrying non-picklable state, don't survive a multiprocessing round trip) — sending a `(type_name, message)` string pair over the pipe and reconstructing a generic `RuntimeError` on the parent side is the same trade-off Python's own `concurrent.futures.ProcessPoolExecutor` makes for exceptions that can't be faithfully re-raised. The original type and message are preserved in the string, just not as the original exception class.

## 8. Alternative Designs

| Alternative | Why not |
|---|---|
| **Container-based isolation (Docker) from v0.1** | Real isolation of filesystem and network that `SubprocessSandbox` doesn't provide — but it's a new infrastructure dependency (a container runtime available at request-serving time) for a platform whose only real tool today is a pure calculator. Same "simplicity before cleverness, don't build ahead of evidence" reasoning module 04 already used to reject sandboxing entirely at that point; this module is the point where *some* isolation earned its build, not necessarily the strongest possible isolation. |
| **`RLIMIT_AS` applied to the parent process around the call (no subprocess at all)** | Would cap the entire Gateway/Runtime process's memory for the duration of every tool call, and a hung or crashed tool call would take the whole request-serving process down with it — no isolation of the "blast radius" at all, just a global limit shared by everything. |
| **A persistent worker pool with a fixed memory ceiling set once at startup** | Cheaper per call (no process-spawn overhead) but couples every call to whatever ceiling the pool was created with, and a call that leaves the worker's memory or state dirty affects every subsequent call routed to that worker. Rejected for the same reason a fresh child was chosen: correctness and isolation over per-call latency, until there's evidence the overhead actually matters. |
| **Silently failing (raising) when `RLIMIT_AS` can't be set, instead of degrading** | Would make the Sandbox unusable during local development on macOS while still being safe to use in the project's real CI/deployment target (Linux) — optimizing for "never silently do less than promised" over "actually usable for day-to-day development," when the honest middle ground (degrade, and say so loudly in code and docs) serves both without lying about either. |

## 9. Trade-offs

**Gained:** a tool call that hangs no longer hangs the request indefinitely — it's killed and surfaced as a typed `SandboxTimeoutError` within a configured timeout. On Linux, a tool call that tries to consume unbounded memory is killed and surfaced as `SandboxResourceLimitError` instead of pressuring the whole platform process. Adopting the Sandbox is opt-in and additive — `RuntimeEngine` behaves exactly as before for every caller that doesn't configure one, so this ships without touching any existing test or caller that predates it.

**Cost:** every sandboxed tool call now pays real process-spawn latency (on the order of tens of milliseconds, not free) — acceptable for tools that do meaningful work, wasteful if applied indiscriminately to something as cheap as arithmetic. The memory ceiling is real on Linux and a no-op on macOS/Darwin — anyone developing or testing locally on a Mac needs to know that fact rather than assume parity with production, which is exactly why it's called out this explicitly rather than buried in a comment. Filesystem and network access remain completely unisolated on every platform — this module bounds *time* and (on Linux) *memory*, nothing else, and should not be represented as "the tool is sandboxed" in the fuller sense a container or VM boundary would provide.

## 10. Production Evolution

```
v0.1 (this module)
  subprocess-per-call, spawned fresh each time
  wall-clock timeout enforced on every platform
  memory ceiling enforced on Linux only (no-op on macOS/Darwin)
  no filesystem or network isolation
  sandboxing is all-or-nothing per RuntimeEngine instance
        │
        ▼
v0.2
  per-tool sandbox policy (declare required limits, or "no sandbox
    needed", per registered Tool rather than one platform-wide setting)
  configurable limits per call (today: fixed at SubprocessSandbox
    construction, not variable per tool_call)
  structured resource-usage reporting (peak memory, CPU time) attached
    to the trace span the tool call already produces
        │
        ▼
Enterprise version
  container-based Sandbox implementation (a second class behind the
    same Protocol) for tools that need real filesystem/network isolation
  network egress allowlist/denylist per tool
  audit logging of every sandbox violation (timeout, memory limit) as
    a security-relevant event, not just an error response
        │
        ▼
Large-scale platform
  sandboxed execution scheduled onto a separate worker fleet, not
    inline in the request-serving process at all — the same shift
    from "in-process" to "dedicated infrastructure" this platform's
    other modules (tracing, evaluation) are likely to make eventually
  cross-platform parity for resource limits (cgroups on Linux,
    Job Objects on Windows, whatever actually works uniformly), so a
    ceiling means the same thing regardless of where the platform runs
```

The scaling challenge here mirrors module 04's: this is a trust-boundary problem, and each step is about safely expanding what an untrusted tool call is allowed to do without expanding what it's allowed to *damage*. v0.1 draws the smallest boundary that's still real (time everywhere, memory on the platform that matters) — everything after that is either widening the boundary (containers, network policy) or making the boundary's guarantees uniform across every environment the platform might run in.

## 11. Real-world Examples

- **OpenHands (formerly OpenDevin)** — runs agent actions inside a dedicated sandboxed runtime (Docker-based) with a controlled filesystem view, the direct real-world analog of what a future container-based `Sandbox` implementation in this module would look like.
- **E2B / Modal sandboxes** — hosted, ephemeral micro-VM or container sandboxes purpose-built for running LLM-generated code; the "sandbox as a service" end state this module's Protocol is deliberately compatible with (a `Sandbox` implementation could call out to one of these instead of spawning a local subprocess).
- **AWS Lambda / Firecracker** — resource-limited, time-bounded, single-invocation execution environments at massive scale; the same timeout-plus-resource-ceiling shape this module implements locally, at a level of isolation (microVMs) well beyond `RLIMIT_AS`.
- **`multiprocessing.Pool` / `concurrent.futures.ProcessPoolExecutor`** — the standard-library building blocks this module's subprocess-per-call approach is built directly on top of, minus the pooling (deliberately, per the Alternative Designs discussion above).
- **OWASP LLM Top 10 — Excessive Agency** — the risk category this entire module exists to bound: an LLM that can trigger real-world actions needs an enforced boundary on what those actions can do, independent of how well-behaved the model usually is.

## 12. Common Mistakes

- **Assuming a resource limit works identically across operating systems.** This module's own implementation hit exactly this: `RLIMIT_AS` is a no-op on macOS/Darwin despite `resource` reporting it as settable. Never assume a POSIX API behaves identically on every POSIX-like platform without checking — verify on the platform you actually deploy to.
- **Applying a resource limit to the wrong process.** Setting `RLIMIT_AS` on the parent (Gateway/Runtime) process instead of the child would cap the whole platform's memory, not the one untrusted call — the limit has to apply to exactly the process that's about to run untrusted code.
- **Setting a limit after the risky code has already started running.** A ceiling applied reactively (e.g., checking memory usage in a monitoring loop and killing the process after the fact) can't prevent the allocation that already happened — limits need to be in place *before* untrusted execution begins.
- **Treating "the memory ceiling is enforced" as "the tool is fully isolated."** This module bounds time and (on Linux) memory — it does not sandbox the filesystem or network. Calling this "full sandboxing" in documentation or in a security review would overstate the actual guarantee.
- **Silently swallowing a platform-limitation error instead of degrading loudly.** `_apply_memory_limit` catches the Darwin `ValueError`, but it does so as one specific, documented, intentional case — a broad `except Exception: pass` anywhere near a security boundary hides real bugs behind "it's probably just the platform thing."

## 13. Best Practices

- Apply resource limits before untrusted code runs, in the process that will actually execute it — not around it, and not on the wrong process.
- Verify platform-specific behavior of low-level APIs (rlimits, process signals) empirically, on the actual deployment target, rather than trusting documentation or `getrlimit` output alone.
- Keep a sandbox boundary as an optional collaborator injected at composition time, not baked into the thing it protects — the tool being sandboxed should never need to know it's being sandboxed.
- Distinguish "a limit was violated" from "the protected code has an ordinary bug" in the error surface — they call for different responses (tune the limit vs. fix the tool) and should never be conflated into one generic failure.
- Be explicit, in code comments and documentation, about what a sandbox does *not* cover — an overstated security boundary is worse than an honestly incomplete one, because it invites trusting a guarantee that isn't there.

## 14. Knowledge

**Must Know**
- Why a model's tool-call arguments are untrusted input, and why that makes tool execution a security boundary, not just an orchestration detail.
- The difference between a wall-clock timeout and a resource (memory/CPU) ceiling, and why both are needed independently — a fast tool can still exhaust memory, and a memory-bounded tool can still hang.
- Why applying a limit *before* untrusted code runs is fundamentally different from checking resource usage *after* the fact.

**Good to Know**
- How `RLIMIT_AS` (and rlimits generally) work on POSIX systems, and that their actual enforcement is not guaranteed uniform across POSIX-like platforms (the macOS/Darwin gap this module hit directly).
- Why `multiprocessing`'s `"spawn"` start method requires targets and arguments to be picklable, and what that implies for where fake/test objects used across a process boundary must be defined (module-level, not nested in a function).
- The trade-off between spawning a fresh process per call (isolation, correctness) versus a pooled worker (lower per-call latency, shared state risk).

**Advanced**
- Container- and microVM-based isolation (Docker, Firecracker, gVisor) as the next tier of guarantee beyond process-level rlimits — what specifically they add (filesystem and network isolation) that this module's v0.1 does not.
- Designing a per-tool sandbox policy layer (which tools need which limits, or no sandboxing at all) as a registry-level concern layered above a generic `Sandbox` Protocol, without coupling the protocol itself to any one tool's requirements.
- Cross-platform resource-limiting strategies (cgroups on Linux, Job Objects on Windows) for a platform that might need uniform guarantees across deployment targets it doesn't fully control.

## 15. Key Takeaways

1. A tool call's arguments are chosen by the model, which makes tool execution untrusted input by construction — Sandbox is the enforced boundary this platform didn't have between that untrusted call and the host process running it.
2. `Sandbox` is a Protocol with one method (`run(tool, kwargs) -> SandboxResult`), and `RuntimeEngine` takes it as an optional collaborator — the same additive, opt-in pattern already used for `Tracer` and `MemoryStore`, so adopting it changes nothing for callers who don't configure one.
3. `SubprocessSandbox`'s v0.1 design (fresh child process per call, memory ceiling applied before the tool runs, wall-clock timeout enforced by the parent) isolates CPU, time, and — on Linux — memory, but explicitly does not isolate filesystem or network access.
4. The memory ceiling this module enforces is a genuine no-op on macOS/Darwin (`RLIMIT_AS` can't actually be lowered there), discovered by running the tests, not assumed — the code degrades honestly rather than crashing every call, and the test suite reflects the gap explicitly instead of hiding it.
5. Sandboxing untrusted execution is a trust-boundary problem, not a feature checklist — each future step (containers, per-tool policy, network allowlists) is about safely widening what a tool call is allowed to do without widening what it's allowed to damage.

## Further Reading

1. Python docs — [`resource` module](https://docs.python.org/3/library/resource.html) — the rlimit primitives `SubprocessSandbox` is built on, including the platform-dependent availability this module hit in practice.
2. Python docs — [`multiprocessing` — Contexts and start methods](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods) — why `"spawn"` was chosen and what it requires of picklable targets/arguments.
3. OWASP — [Top 10 for LLM Applications: Excessive Agency](https://owasp.org/www-project-top-10-for-large-language-model-applications/) — the risk category this entire module exists to bound, already referenced in module 04's tutorial.
4. OpenHands (GitHub) — [runtime/sandbox implementation](https://github.com/All-Hands-AI/OpenHands) — a real, production open-source example of container-based agent action isolation, the natural next tier beyond this module's v0.1.
5. Firecracker — [microVM design overview](https://firecracker-microvm.github.io/) — background on the strongest tier of isolation (microVMs) referenced in this tutorial's Real-world Examples and Advanced Knowledge sections.

## Next Module

There isn't a next module in the original nine — Sandbox is the platform's first extension beyond its initial complete roadmap (Gateway through CLI Client), added specifically to close the tool-execution trust gap module 04 flagged and deliberately deferred. The natural next piece of platform-vs-application-role work, per the same enterprise-extension proposal that motivated this module, is a **Registry Expert**-scoped effort: broadening today's `ToolRegistry` (tools only) into a unified Capability registry spanning tools, providers, and evaluation graders — making "Everything is a Capability" actually true platform-wide, not just for tools.
