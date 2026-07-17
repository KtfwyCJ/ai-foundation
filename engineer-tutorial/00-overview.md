# 00 — Platform Overview: How the Whole System Fits Together

*Internal onboarding doc — AI Platform, cross-module synthesis*

This is not a tenth module. It's the map that ties the other nine together — read it first, then use `01`–`09` for depth on any one box.

## 1. Executive Summary

`ai-foundation` is a small, production-shaped AI platform: a FastAPI Gateway in front of an agentic Runtime that talks to a model provider, can call tools, remembers conversations, records traces, and can be graded offline — all shippable as a container with CI. It was built **module by module, outward to inward and back out**: Gateway first (the boundary), then Provider (talk to a vendor), then Runtime (compose the two), then Tools, Memory, Tracing bolted onto Runtime one at a time, then Evaluation (grade Runtime's output) and Deployment (package the whole thing), then a CLI client (prove the HTTP boundary is real by building a caller that only speaks HTTP).

The one idea that explains almost every file in this repo: **every module is a `Protocol` interface plus exactly one real implementation, injected into whatever depends on it.** Six different concerns (`RuntimeClient`, `ModelProvider`, `Tool`, `MemoryStore`, `Tracer`, `Grader`) all use the identical shape. Once you recognize that pattern once, you've understood the architecture of the whole codebase — the rest is which concern each interface owns.

## 2. The Problem

An AI platform that "just calls the model API from a route handler" works for a demo and fails as a system, for reasons that show up in a predictable order as usage grows:

- No boundary between "is this caller allowed to be here" and "what do we do for them" → auth/quota logic gets copy-pasted into every endpoint.
- No boundary between "the platform's own logic" and "a specific vendor's wire format" → adding a second model provider means rewriting call sites, not adding a class.
- No way for a model to take real action (calculate, look something up) → the system is a text toy, not an agent.
- No server-side conversation state → every client has to replay full history, including tool exchanges, on every call.
- No visibility into what happened on a request → debugging a slow or failed call is guesswork, and cost is unmeasured.
- No repeatable way to check if the *answers* are still good after a prompt/model/tool change → regressions in quality are invisible until a human notices.
- No reproducible way to run the system outside one developer's machine → "works for me" isn't "works."

Each module in this repo exists to close exactly one of these gaps, in the order they'd actually bite a team building this for real.

## 3. Motivation

Enterprise systems solve the problems above the same way, over and over: identify a concern, put an interface in front of it, ship the simplest real implementation behind that interface, and make the next layer up depend on the interface — never the concrete class. This repo applies that once per concern instead of inventing six different patterns for six different problems. The payoff shows up structurally: **Runtime is the only module that talks to everything else**, and every other module (Gateway, Provider, Tools, Memory, Tracing, Evaluation) can be built, tested, and reasoned about in isolation, against a fake, with no real network call, real API key, or real cost.

## 4. Responsibilities (platform-level)

**The platform should:**
- Expose exactly one HTTP contract (`POST /v1/chat`, `ChatRequest`/`ChatResponse`) that never changes shape as internal capability grows
- Keep every internal concern (which vendor, which tools, which storage, whether it's traced) invisible to the caller and swappable without touching the caller-facing layer
- Let each module be tested against a fake standing in for everything below it — no module's tests require a real Anthropic key or network access
- Grow by addition (a new class registered somewhere) rather than by edits to existing orchestration logic

**The platform should NOT:**
- Let any module reach two layers away (e.g. the Gateway importing the Provider layer directly, or a `Tool` calling back into Runtime)
- Build speculative abstractions ahead of a second real implementation (one provider, one grader, one tool, one memory backend — each module says this explicitly and means it)
- Solve problems that haven't been hit yet (retries, sandboxing, summarizing long conversations, multi-replica state) — these are named, deferred, and tracked in each tutorial's Production Evolution section, not silently skipped

## 5. Architecture

```
                                   Client
                                     │  HTTP + Bearer token
                                     ▼
                        ┌─────────────────────────┐
                        │        Gateway               │  ai_platform/api/
                        │  auth → rate limit → validate │
                        └────────────┬────────────────┘
                                     │  RuntimeClient (interface)
                                     ▼
                        ┌─────────────────────────┐
                        │        Runtime               │  ai_platform/runtime/
                        │  the only module that talks    │
                        │  to everything below it         │
                        └───┬─────────┬─────────┬──────┘
                            │           │           │
              ModelProvider │  ToolRegistry│  MemoryStore│      Tracer (optional,
                            ▼           ▼           ▼        wraps every provider/
                   ┌────────────┐┌───────────┐┌───────────┐   tool call)
                   │ Anthropic  ││ Calculator ││ InMemory  │
                   │ Provider   ││ Tool       ││ Store     │
                   └────────────┘└───────────┘└───────────┘
                            │
                            ▼
                        Claude API

              Evaluation (ai_platform/evaluation/)
              runs EvalCases through any RuntimeClient
              and grades responses — sits beside the
              request path, never inside it

              Deployment (Dockerfile, docker-compose.yml,
              .github/workflows/ci.yml)
              packages the whole graph above; adds no
              application code of its own

              CLI Client (ai_platform/client/)
              a leaf caller that only speaks HTTP —
              proof the Gateway's contract is sufficient
```

Six Protocol/implementation pairs recur throughout: `RuntimeClient`→`RuntimeEngine`/`EchoRuntimeClient`, `ModelProvider`→`AnthropicProvider`, `Tool`→`CalculatorTool`, `MemoryStore`→`InMemoryStore`, `Tracer`→`InMemoryTracer`, `Grader`→`ContainsGrader`. Every arrow in the diagram above is an interface, not a concrete import.

## 6. Request Flow (the whole system, one request)

Walking a `POST /v1/chat` with a `conversation_id`, a question needing the calculator, all the way through:

1. **Gateway**: `verify_api_key` → `enforce_rate_limit` → `ChatRequest` validation (pydantic) → route handler gets a validated request plus an injected `RuntimeClient`.
2. **Runtime — load**: if `conversation_id` is set, `MemoryStore.load()` returns prior turns; `messages = history + request.messages`.
3. **Runtime — trace start**: `trace_id = conversation_id or uuid4()`, grouping every span this request (or conversation) produces.
4. **Runtime → Provider**: `ModelProvider.complete(messages, model, tools=registry.definitions())`, timed and recorded as a `"provider.complete"` span. `AnthropicProvider` splits out `system`, calls Claude, maps SDK exceptions to `ProviderError` subclasses, returns a `ProviderResponse`.
5. **Tool loop**: if the response carries a `tool_use` block, Runtime appends it to `messages`, looks the tool up in `ToolRegistry.get(name)`, executes it (timed as a `"tool.execute"` span), appends a `tool_result` message, and calls the Provider again — up to 5 iterations before `RuntimeToolLoopExceededError`.
6. **Runtime — persist**: once a final, non-tool answer comes back, `MemoryStore.append(conversation_id, messages[len(history):])` writes only what's new since step 2 — nothing is persisted if the loop never converged.
7. **Runtime → Gateway**: `ChatResponse` is shaped from the final message and returned; any `ProviderError` raised anywhere above propagates unchanged to the Gateway's single exception handler, which maps it to the right HTTP status by type — no re-wrapping at any layer.
8. **Off to the side, anytime**: an `EvalRunner` can replay a fixed set of `EvalCase`s through this exact same `RuntimeClient.handle_chat` path and grade the answers; `InMemoryTracer.get_trace(trace_id)` can return every span this request produced. Neither is on the request's critical path.

The Gateway's external contract (`ChatRequest` in, `ChatResponse` out) is identical whether zero, one, or five tool iterations happened, whether memory was used, and whether a tracer was wired in at all.

## 7. Design Decisions (the ones that repeat across every module)

**Why `Protocol` instead of ABCs everywhere?** Structural typing — a test fake just needs the right method signature, no inheritance required. Used consistently rather than mixing patterns module to module.

**Why constructor injection instead of a service locator or globals?** Every dependency (`ModelProvider`, `ToolRegistry`, `MemoryStore`, `Tracer`) is passed into `RuntimeEngine.__init__`, wired in exactly one place (`api/dependencies.py`). Tests substitute fakes there; production substitutes real implementations there. No module reaches for global state to find its own dependency.

**Why does Runtime never re-wrap errors from the layers below it?** `ProviderError` and friends already live on the shared `PlatformError` hierarchy, and the Gateway's error handler already maps every subclass to an HTTP status by type. Re-wrapping at each layer would either lose that granularity or duplicate the mapping table for no benefit — so nothing above Provider catches and re-raises.

**Why is almost everything optional or additive rather than a breaking change?** `conversation_id` is optional (skips Memory entirely if absent); `Tracer` is optional (`if not self._tracer: return` at every call site); tools are additive (`registry.register(...)`, no edits to Runtime). Every module was added without changing the Gateway's external contract or breaking a test written before it existed.

## 8. Alternative Designs (rejected once, at the platform level)

| Alternative | Why not |
|---|---|
| **One big `RuntimeEngine`-style class per concern, with everything hardcoded (one vendor, one tool, one store) and no interface** | Works today, fails the moment a second implementation is needed — every module tutorial names this same rejection independently (hardcode `AnthropicProvider`, hardcode tool dispatch, hardcode `InMemoryStore`). The interface costs almost nothing when there's one implementation and pays for itself the moment there's a second. |
| **A single "God" orchestrator class that imports Provider, Tools, Memory, and Tracing concretely** | This is what `RuntimeEngine` would be without the Protocol boundaries — it would still *work*, but every test of Runtime's orchestration logic would need a real Anthropic key, a real tool, a real store. The interfaces are what make `tests/runtime/` fast and free. |
| **Wrap a third-party unification framework (LiteLLM, LangChain, an agent framework) instead of building these boundaries by hand** | Legitimate for a real product — rejected here on purpose, because the point of this repository is to understand and own each boundary, not depend on someone else's. Explicitly named as worth revisiting in the Provider tutorial once a second/third vendor is actually needed. |
| **Build the "enterprise" version of each module immediately** (retries, sandboxing, Redis-backed memory/tracing, LLM-as-judge grading, multi-provider routing) | Every tutorial's Production Evolution section shows the same discipline: ship the thinnest correct version, name the gap explicitly, and let a real requirement — not speculation — drive the next version. |

## 9. Trade-offs

**Gained:** nine independently testable, independently understandable modules; a Gateway contract that hasn't changed shape since module 01 even though Runtime's internal capability has grown by roughly 5x; a codebase where "add a second model provider" or "add a second tool" is additive, not a refactor; 71 tests that all run without a real API key.

**Cost:** the platform is, deliberately, behind where a "real" production system would be — single-process `InMemoryStore`/`InMemoryTracer` (gone on restart, disjoint across replicas), no retries anywhere, no sandboxing for tools, a substring-matching grader, no streaming. None of these are oversights; each is named in its module's own tutorial as the next thing to build once a real requirement demands it. The risk of this approach is only that "later" has to actually happen — a codebase that defers everything and never circles back accumulates the same debt any other codebase would.

## 10. Production Evolution (platform-level)

```
v0.1 (this repo, today)
  one provider (Anthropic), one tool (calculator), one memory
  backend (in-process dict), one tracer (in-process), one grader
  (substring match) — every interface proven by exactly one real
  implementation, composed through Runtime, packaged as one
  container, tested by 71 fake-backed tests
        │
        ▼
v0.2 (named in each module's own tutorial)
  second ModelProvider (proves the abstraction under real pressure)
  retry/backoff policy at the Provider or Runtime layer
  tool-error recovery (feed a failed tool call back to the model)
  Redis- or Postgres-backed MemoryStore/Tracer for multi-replica runs
  streaming responses (Gateway → Runtime → Provider)
  LLM-as-judge Grader alongside ContainsGrader
        │
        ▼
Enterprise platform
  auth/RBAC (which caller can use which model/tool/conversation —
    flagged as out of scope in every module that touches identity)
  cost accounting fed by Tracing's already-recorded token usage
  model routing/fallback across multiple registered providers
  a real golden eval dataset with CI-gated quality thresholds
  published, versioned container images; multi-region deployment
```

## 11. Real-world Examples

- **LiteLLM** solves exactly the Provider layer's problem (one call signature over many vendors) as a standalone library — this repo's `ModelProvider` is a hand-rolled, single-vendor version of the same idea, explicitly kept small on purpose.
- **LangGraph** / **OpenAI Agents SDK** generalize this platform's tool-calling loop (`RuntimeEngine`'s "call model, check for tool_use, execute, re-call") into a full graph/state-machine abstraction for much more complex agent flows than a bounded 5-iteration loop.
- **Langfuse** is what `Tracer`/`Span` are a minimal version of — a real observability backend for LLM calls, with export, aggregation, and a UI on top of the same "record what happened per call" idea.
- **Dify** packages Gateway + Runtime + Tools + a UI into one product — useful as a reference for what this platform's pieces look like once there's a frontend and a multi-tenant admin layer on top.

## 12. Common Mistakes

- Treating "Runtime calls Provider" and "Runtime calls Tools/Memory/Tracing" as different kinds of relationships — they're the same DI pattern, six times. Missing that makes the codebase look like six unrelated modules instead of one repeated idea.
- Assuming a module's v0.1 scope (one vendor, one tool, in-memory storage) is an oversight rather than a deliberate, load-bearing decision — every tutorial explains *why* the narrow scope was chosen, and building ahead of it means guessing at a shape with no evidence.
- Catching and re-wrapping exceptions at every layer "for safety" — this repo's error hierarchy is designed to propagate unchanged from Provider to Gateway; adding a wrapper at Runtime would silently destroy the granularity the Gateway's error handler depends on.
- Reaching for a shared/global instance of a dependency instead of using the DI wiring already established in `api/dependencies.py` — breaks test isolation and reintroduces the service-locator anti-pattern this codebase avoids everywhere.

## 13. Best Practices

- One `Protocol` per external concern, one real implementation behind it, injected — not imported — into whatever depends on it.
- Keep transport-agnostic errors (`PlatformError` and subclasses) separate from the one layer that translates them to a transport (HTTP, in the Gateway) — so the same exception types would work unchanged behind gRPC or a queue consumer.
- Let the orchestrator (`RuntimeEngine`) be the only module that knows about every other module; keep every other module mutually unaware of its siblings.
- Name deferred scope explicitly (a Production Evolution section, a comment, a tutorial) rather than leaving it to be rediscovered as a silent gap later.

## 14. Knowledge

### Must Know
- Dependency inversion via `Protocol`/structural typing, and why it enables testing without real backends.
- The gateway/BFF pattern: separating "should this request proceed" from "what does it mean and how do we fulfill it."
- Why LLM tool-calling requires echoing `tool_use` blocks back verbatim as conversation history, and why that forced `ChatMessage.content` to become a union type.
- The difference between a unit test (scripted dependency), an integration test (real backend), and an evaluation run (real backend, output *quality* judged, not just code correctness).

### Good to Know
- Why span-based tracing (one span per unit of work, `trace_id` grouping) beats ad-hoc logging for both debugging and future cost/quality dashboards.
- Why persisting only `messages[len(history):]` (not the whole list) after a tool loop avoids duplicating history on every turn.
- Why a multi-stage Docker build (builder stage vs. slim runtime stage) matters for image size and attack surface, independent of the application code itself.

### Advanced
- Designing an interface (`ModelProvider`, `Grader`) against exactly one real implementation without over-generalizing for hypothetical second implementations — judging when there's "enough evidence" to add abstraction.
- The concurrency/consistency implications of single-process, in-memory state (`InMemoryStore`, `InMemoryTracer`, the rate limiter) once a system moves to multiple replicas — and what has to change (external store, sticky routing, or a distributed backend) versus what doesn't.
- Separating infrastructure failure ("error," in Evaluation's terms) from output-quality failure ("failed grade") as genuinely different signals requiring different remediation.

## 15. Key Takeaways

1. **One pattern, six times**: every module is a `Protocol` plus one real implementation, injected into the layer above it — recognize this once and the whole architecture is legible.
2. **Runtime is the only module that talks to everything else** — Gateway, Provider, Tools, Memory, Tracing, and Evaluation are all mutually unaware of each other.
3. **The external contract (`ChatRequest`/`ChatResponse`) never changed shape** as internal capability grew roughly 5x — that stability is the entire point of the Gateway/Runtime split from module 01.
4. **Every module names its own deferred scope explicitly** (Production Evolution sections) — narrow v0.1 scope is a decision, not an oversight, and the next steps are already written down.
5. **Errors are transport-agnostic and propagate unchanged** from Provider through Runtime to the Gateway's single, type-based mapping — no layer re-wraps what a layer below it already got right.

## Further Reading

1. Official Anthropic Messages API docs (tool use / system prompt shape) — the closest thing to a spec for the one vendor quirk this whole platform is built around.
2. FastAPI's dependency injection docs (`Depends()`) — the mechanism behind every wiring point in `api/dependencies.py`.
3. LiteLLM's source (provider translation layer) — a production-scale version of what `ai_platform/providers/` does for one vendor.
4. Langfuse or OpenTelemetry docs — where `Tracer`/`Span` would grow into if given a real export target.
5. Martin Fowler's writing on the Backend-for-Frontend pattern — the classic framing behind the Gateway/Runtime split.

## Next Module

Read `01-gateway.md` through `09-cli-client.md` in order — they were written and built in that sequence, and each one's "Request Flow" section picks up exactly where the previous module's left off. If you only have time for the ones that changed this codebase's shape the most, read `03-runtime.md` (the composition point) and `04-tool-registry.md` (the first module that forced a real type change) first.
