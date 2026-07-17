# 06 — The Tracing / Observability Module

*Internal onboarding doc — AI Platform, Tracing component (`ai_platform/tracing/`)*

## 1. Executive Summary

Every module built so far makes Runtime *do* more — call a real model, call tools, remember conversations. None of them make Runtime *observable*. Until this module existed, a request that took 8 seconds and a request that took 800ms looked identical from the outside; a `ProviderResponse` has carried `input_tokens`/`output_tokens` since the Provider module, and nothing has ever read them. If a request failed, the only signal was the exception that propagated to the Gateway's error handler — there was no record of *which* step failed, *how many* provider calls or tool executions happened first, or what any of them cost.

Tracing closes that gap. It defines a `Tracer` protocol and a `Span` — one unit of recorded work (a provider call or a tool execution) — and makes `RuntimeEngine` record a span around every one of those calls, tagged with a `trace_id` that groups everything that happened on one request or conversation. `InMemoryTracer` is the v0.1 sink; the interface is what lets it become a real backend (OpenTelemetry, Datadog, Langfuse) without touching `RuntimeEngine` again.

## 2. The Problem

An LLM-backed request isn't one operation, it's a small pipeline: load history, call a model, maybe call a tool, maybe call the model again, persist the result. Each step has its own latency, its own failure mode, and — for the model call specifically — its own cost in tokens. Without tracing, none of that is visible after the fact:

- **Debugging "why was this request slow"** means guessing whether it was the model, a tool, or memory — there's no timing breakdown.
- **Debugging "why did this fail"** means reading a stack trace with no context about which provider call or tool call it was, or how many had already succeeded on that request.
- **Cost accounting is impossible.** `ProviderResponse.input_tokens`/`output_tokens` exist on every response but are discarded the moment `RuntimeEngine` reads `result.message` and `result.tool_calls` off of them.
- **Evaluation has nothing to build on.** Measuring output quality (the next module) needs to know what actually ran on a request — which model, how many tool-loop iterations — and today that information doesn't outlive the request.

## 3. Motivation

Every production LLM platform separates "did the request succeed" (the Gateway's HTTP response) from "what happened while it was being processed" (a trace). The two answer different questions for different audiences: the caller wants a chat response; an engineer debugging a regression, an SRE watching latency, or a finance team doing cost accounting wants the trace. Bolting that visibility onto `RuntimeEngine` as ad-hoc logging would work once, but every future module (Evaluation needs to know what ran; a future admin API would want to expose traces; a future cost dashboard would aggregate span attributes) would have to parse log lines to get it back out. A structured `Span` recorded through a `Tracer` interface means all three "consumers" — a debugger, a dashboard, an evaluation harness — read the same typed data.

This is the same shape decision this platform has made three times already (`ModelProvider`, `ToolRegistry`, `MemoryStore`): identify the concern, put a `Protocol` in front of it, ship the simplest real implementation, and let `RuntimeEngine` depend on the interface only.

## 4. Responsibilities

**Tracing should:**
- Define what a unit of traced work looks like (`Span` — `types.py`): a `trace_id`, a `name`, a `duration_ms`, free-form `attributes`, and an optional `error`
- Define the `Tracer` protocol (`interfaces.py`): a single `record(span)` method — a pure sink, nothing more
- Ship a real, testable v0.1 sink (`InMemoryTracer` — `in_memory.py`) that stores spans keyed by `trace_id` and can return them via `get_trace()`

**Tracing should NOT:**
- Decide *when* to start or stop timing something — that's the caller's job (`RuntimeEngine` wraps each provider/tool call itself)
- Choose the `trace_id` — `RuntimeEngine` does, using `request.conversation_id` when present so a whole conversation's spans are groupable, and a generated id otherwise
- Sample, batch, or export spans anywhere — `InMemoryTracer` records synchronously and unconditionally, real sampling/export strategy is a v0.2 concern once there's a real backend to export to
- Aggregate spans into metrics (p50/p99 latency, cost per model) — that's what a metrics/dashboard layer built *on top of* recorded spans would do; Tracing's job stops at "record what happened," not "summarize what happened"

## 5. Architecture

```
                 RuntimeEngine
                    │  trace_id = conversation_id or uuid4()
                    │
                    │  around provider.complete()  →  tracer.record(Span("provider.complete", ...))
                    │  around tool.execute()        →  tracer.record(Span("tool.execute", ...))
                    ▼
      ┌─────────────────────────────┐
      │   Tracer (Protocol)             │   ai_platform/tracing/interfaces.py
      │   record(span) -> None          │
      └──────────────┬────────────────┘
                      │ implemented by
                      ▼
      ┌─────────────────────────────┐
      │   InMemoryTracer                │   ai_platform/tracing/in_memory.py
      │   { trace_id: [Span, ...] }     │
      └─────────────────────────────┘

      ┌─────────────────────────────┐
      │   Span                          │   ai_platform/tracing/types.py
      │   trace_id, name, duration_ms,  │
      │   attributes, error              │
      └─────────────────────────────┘
```

Upstream: `RuntimeEngine` holds an optional `Tracer` reference (constructor-injected, same DI pattern as `ModelProvider`/`ToolRegistry`/`MemoryStore`), and is the *only* module that ever constructs a `Span` — Tracing itself has no notion of "provider" or "tool," those are just string names `RuntimeEngine` chooses. Downstream: nothing — `InMemoryTracer` is a leaf, just like `InMemoryStore`. Sideways: unlike Memory, Tracing is unconditionally optional — `tracer: Tracer | None = None` — and every call site checks `if not self._tracer: return` before doing any work, so a Runtime wired without a tracer pays zero cost and behaves exactly as it did before this module existed.

## 6. Request Flow

Continuing the tool-calling example from the Tool Registry tutorial, now with a `Tracer` wired in:

1. **`RuntimeEngine.handle_chat`** computes `trace_id = request.conversation_id or str(uuid.uuid4())` once, before the loop starts — every span this request produces, however many tool-loop iterations it takes, is grouped under this one id.
2. **First `self._complete(trace_id, messages, model, tools)`** times `provider.complete(...)` with `time.monotonic()`, and on success calls `self._record_span(...)` with `name="provider.complete"` and `attributes={"model", "stop_reason", "input_tokens", "output_tokens"}` — the exact fields the Provider tutorial flagged as "ready for cost accounting" finally get read.
3. **The model requests the calculator tool.** `RuntimeEngine` calls `self._run_tool_calls(trace_id, result.tool_calls)`, which times each `tool.execute(...)` call individually and records a `"tool.execute"` span with `attributes={"tool": "calculator"}`.
4. **Second `self._complete(...)`** — another `"provider.complete"` span recorded, same `trace_id`.
5. **`RuntimeEngine`** returns the final `ChatResponse`, exactly as before — the caller-facing contract is completely unchanged; tracing is a side channel, not a response field.
6. **If a provider call raises** (e.g. `ProviderTimeoutError`), `self._complete` still records a `"provider.complete"` span — with `error=str(exc)` and no token attributes, since none exist — and then re-raises the original exception unchanged, so the Gateway's existing error-to-HTTP mapping is untouched.
7. **If a tool call raises** (an unregistered tool, or the tool's own code), `self._run_tool_calls` records a `"tool.execute"` span with the error before re-raising — so a failed request still leaves behind a trace explaining what was attempted.
8. **After the request**, `await tracer.get_trace(trace_id)` (on `InMemoryTracer`) returns every span recorded for that id, in order — the full timeline of one request or, if `conversation_id` was reused, one conversation.

## 7. Design Decisions

**Why is `Tracer.record` a single flat method instead of a `start_span()`/`end_span()` pair, or a context manager?**
Because timing is `RuntimeEngine`'s responsibility, not Tracing's — `RuntimeEngine` already knows exactly when a provider or tool call starts and ends (it's the one making the call), so it can compute `duration_ms` itself with `time.monotonic()` and hand the sink one finished `Span`. A `start`/`end` pair would force `Tracer` implementations to manage in-flight state (what if `end` is never called?) for no benefit v0.1 needs. This mirrors the same "push complexity to the one place that actually has the information" reasoning as `MemoryStore.append` only receiving *new* messages instead of recomputing what's new itself.

**Why is `trace_id` chosen by `RuntimeEngine`, not generated inside `Tracer`?**
`RuntimeEngine` is the only place that knows whether a request has a `conversation_id` (from `ChatRequest`) — reusing it as the `trace_id` is what makes every span across an entire multi-turn conversation retrievable under one id via `get_trace()`. If `Tracer` generated its own ids, spans from the same conversation across different HTTP requests would end up in unrelated groups, and correlating "everything that happened in conversation X" would require a separate lookup table Tracing has no reason to own.

**Why does a failed provider or tool call still get a span (`error` set) instead of no span at all?**
A trace's main value is diagnosing what went wrong — recording nothing on failure would mean the one case tracing matters most (a request that broke) produces the least information. Recording the error on the span, then re-raising the *original* exception unchanged, keeps the Gateway's error handling exactly as it was while adding a durable record of what was attempted before the failure.

**Why is `Tracer` fully optional (`tracer: Tracer | None = None`) rather than defaulting to an always-present no-op tracer?**
Both work; a "null object" `NoOpTracer` would remove the `if not self._tracer: return` checks scattered through `RuntimeEngine`. This module chose the explicit `None` check for consistency with `MemoryStore`, which made the identical choice for the identical reason (`if request.conversation_id and self._memory`) — the platform already has one established pattern for "this dependency is optional," and introducing a second pattern (the null-object sink) for the same kind of optionality would be inconsistency without a real benefit.

**Why `attributes: dict` (untyped) instead of a strongly-typed field per span kind (e.g. `ProviderSpan`, `ToolSpan` subclasses)?**
There are exactly two span kinds today (`provider.complete`, `tool.execute`) with different attribute shapes, and no consumer yet that needs to do more than read attributes back for display or debugging (`get_trace()` output, or a future Evaluation harness). A typed hierarchy is exactly the kind of "design ahead of the second real use case" this platform has repeatedly deferred (see the Provider tutorial's single-vendor decision, or the Tool Registry's single hardcoded iteration cap) — worth revisiting the moment a consumer needs to do type-safe things with span-specific fields.

## 8. Alternative Designs

| Alternative | Why not |
|---|---|
| **Ad-hoc `logging.info(...)` calls sprinkled through `RuntimeEngine`** | Produces unstructured text that a future dashboard, evaluation harness, or cost report would have to parse back out of log lines — a `Span` is the same information as a typed, queryable record instead. |
| **A global/module-level tracer (singleton) instead of constructor injection** | Would make every `RuntimeEngine` instance share one tracer implicitly, break the test isolation every existing test relies on (`InMemoryTracer()` per test, exactly like `InMemoryStore()`), and reintroduce the Service Locator anti-pattern this platform has avoided everywhere else via DI. |
| **Wrap the entire `handle_chat` call in one span instead of one span per provider/tool call** | Would tell you a request took 3 seconds but not *why* — whether that was one slow model call, five fast tool calls, or a tool-loop that iterated four times. Per-call spans are what make the timeline actually diagnostic. |
| **Adopt OpenTelemetry's SDK directly as the `Tracer` type, v0.1** | A real, valuable v0.2 step — but it would mean pulling in an OTel dependency and its context-propagation machinery before this platform has a second consumer (an exporter, a dashboard) to justify it. `Tracer` is deliberately shaped so an OTel-backed implementation is a drop-in `record()` implementation later, without OTel's API leaking into `RuntimeEngine`. |

## 9. Trade-offs

**Gained:** every request's internal timeline (which calls, how long, how many tokens, what failed) is now a structured, retrievable record instead of nothing. `RuntimeEngine`'s tracing logic is independently testable via `InMemoryTracer` without a real model or tool call, exactly like Memory and the tool loop before it. The `ProviderResponse.input_tokens`/`output_tokens` fields that have sat unused since the Provider module now flow somewhere.

**Cost:** `RuntimeEngine` grew two new private helper methods (`_complete`, `_record_span`) and every provider/tool call site now has a `try/except` purely for span recording — real complexity, justified by the fact that "what happened on this request" is a first-class production requirement, not a nice-to-have. `InMemoryTracer`, like `InMemoryStore`, is single-process and unbounded — it will leak memory in a long-running process with no eviction, which is an explicitly named limitation, not an oversight.

## 10. Production Evolution

```
v0.1 (this module)
  one span per provider call and per tool call
  in-memory, single-process, unbounded storage
  no sampling — every call traced, always
  no export — spans only readable via get_trace() in-process
        │
        ▼
v0.2
  OpenTelemetry-backed Tracer implementation (spans exported to a
    collector — Jaeger, Datadog, Honeycomb, etc.)
  sampling strategy (trace every request, or only a percentage /
    only errors, once volume makes "always" too expensive)
  span retention/eviction policy for the in-memory fallback
        │
        ▼
Enterprise version
  cost-per-request and cost-per-tenant rollups built on span
    attributes (input_tokens/output_tokens x per-model pricing)
  latency SLO dashboards and alerting on p95/p99 span duration
  trace-level access control (who can view whose conversation traces)
        │
        ▼
Large-scale platform
  distributed trace propagation across services (a request that
    fans out to multiple internal services, not just one Runtime
    process) via W3C Trace Context headers
  automatic anomaly detection on span patterns (a tool suddenly
    taking 10x longer, a model's token usage spiking) feeding
    the same alerting a Enterprise-tier SRE team would need
```

The scaling challenge here is volume and export, not the data model — `Span` as a shape (id, name, duration, attributes, error) barely changes from v0.1 to large-scale; what changes is where spans go (in-process dict → collector → distributed backend) and how many get kept (all → sampled → policy-driven retention).

## 11. Real-world Examples

- **OpenTelemetry** — the industry-standard spec for exactly this: spans, trace ids, span attributes, and a pluggable exporter architecture. This module's `Tracer`/`Span` shape is a deliberately small, hand-rolled precursor to adopting the real SDK.
- **Langfuse** — an LLM-specific observability platform built on the identical core idea: trace a request's model calls, tool calls, and token usage, and make them queryable after the fact — with a UI purpose-built for LLM traces (prompts, completions, cost) rather than generic APM spans.
- **LangSmith (LangChain)** — traces chains/agents the same way: every LLM call and tool call in a run becomes an inspectable step, grouped under a run id (this module's `trace_id`).
- **Datadog / Honeycomb** — general-purpose APM platforms that any `Tracer` implementation could export to once a v0.2 backend is chosen; this module's `record(span)` interface is what makes swapping to one of these a new class, not a `RuntimeEngine` rewrite.

## 12. Common Mistakes

- **Recording spans only on success.** The failure case is exactly when a trace is most valuable — this module deliberately records an error span before re-raising, on both the provider and tool call paths.
- **Letting a tracing failure break the request.** `InMemoryTracer.record` can't realistically fail, but a real network-backed exporter can — a production `Tracer` implementation should swallow its own transport errors internally rather than let a broken observability backend take down real user traffic. (This module's `_record_span` does not add defensive handling for that today, since `InMemoryTracer` has no failure mode to guard against — a named gap for the moment a real exporter is introduced.)
- **Conflating `trace_id` with `conversation_id` as if they're always the same thing.** They coincide today because it's convenient (a stateful conversation's spans should be groupable), but a stateless request (no `conversation_id`) still gets its own generated `trace_id` — tracing must work independently of whether Memory is in play.
- **Building a metrics/aggregation layer directly into the `Tracer` sink.** Recording and summarizing are different jobs; `InMemoryTracer.get_trace()` returns raw spans, and computing p99 latency or per-model cost from them is a separate concern layered on top, not baked into the sink itself.
- **Adding OpenTelemetry (or any real exporter) before there's a second consumer that needs it.** The same discipline as every prior module: ship the interface and the simplest working implementation first, adopt a heavier real backend once there's evidence for which one.

## 13. Best Practices

- Record timing and outcome (success/error) at the exact call site that has that information — don't try to reconstruct it later from logs.
- Group related spans under a single id (`trace_id`) chosen by the orchestrating layer, not generated independently by the sink.
- Keep the sink (`Tracer`) a pure, dependency-injected interface so the storage/export backend can change without touching the code that produces spans.
- Record spans on the failure path too, with the error captured on the span — then let the original exception propagate unchanged.
- Resist adding span-kind-specific typed fields or aggregation logic until a real consumer needs them.

## 14. Knowledge

**Must Know**
- What a "span" and a "trace" are, and why they're the standard unit of observability for multi-step request processing (this concept predates LLM platforms — it's core distributed-tracing vocabulary).
- Why timing/outcome should be captured at the call site with the most context, and handed to a simple sink, rather than reconstructed after the fact from logs.
- Why LLM-specific tracing needs to capture token usage and model identity, not just latency — cost is a first-class concern this platform's provider layer specifically exposed for this reason.

**Good to Know**
- The difference between recording a span and aggregating spans into metrics (percentiles, cost rollups) — two different, sequential responsibilities.
- Why an optional dependency (`Tracer | None`) is a legitimate alternative to a no-op/null-object default, and when each is preferable.
- How `trace_id` reuse across multiple requests in the same conversation enables correlating an entire multi-turn interaction, not just a single HTTP call.

**Advanced**
- OpenTelemetry's context propagation model (trace context headers) for distributed tracing across service boundaries, and how a single-process `Tracer` like this module's is a precursor to adopting it.
- Sampling strategies (head-based vs. tail-based) for controlling tracing volume/cost at scale, and why "trace everything" stops being viable long before "log everything" does.
- Designing span attribute schemas that stay queryable at scale (e.g. consistent attribute keys across span kinds) versus this module's deliberately loose, per-call-site `dict`.

## 15. Key Takeaways

1. Tracing is what makes a multi-step request (provider call, maybe a tool call, maybe another provider call) observable after the fact — before this module, none of that internal timeline existed anywhere once the response was returned.
2. `Tracer` is a `Protocol` with one method (`record(span)`); `RuntimeEngine` is the only place that constructs `Span`s and the only place that knows when a call starts and ends — the sink stays a pure, dumb store, exactly like `MemoryStore` before it.
3. `trace_id` is `request.conversation_id` when present (grouping a whole conversation's spans together) or a generated id otherwise — chosen by `RuntimeEngine`, never by the sink.
4. Spans are recorded on both success and failure paths — the error case is where a trace is most valuable, and the original exception always propagates unchanged regardless of whether tracing succeeded.
5. `ProviderResponse.input_tokens`/`output_tokens`, unused since the Provider module, are now captured on every `"provider.complete"` span — the concrete gap this module was built to close, and the foundation cost accounting and Evaluation will build on next.

## Further Reading

1. OpenTelemetry — [Traces](https://opentelemetry.io/docs/concepts/signals/traces/) (official docs) — the industry-standard model this module's `Span`/`Tracer` is a small precursor to.
2. Langfuse — [Tracing for LLM applications](https://langfuse.com/docs/tracing) — the closest real-world analog: LLM-specific traces covering model calls, tool calls, and token/cost data.
3. LangSmith — [Tracing concepts](https://docs.smith.langchain.com/old/tracing) — another direct analog, tracing chains/agents as nested runs under a shared id.
4. Google — [Dapper, a Large-Scale Distributed Systems Tracing Infrastructure](https://research.google/pubs/pub36356/) (paper) — the foundational paper behind modern distributed tracing, background for why `trace_id`-grouped spans became the standard shape.
5. Martin Fowler — [Focusing on Observability](https://martinfowler.com/articles/domain-oriented-observability.html) — background on why domain-meaningful telemetry (not just generic APM) matters, relevant to this module's choice to record `model`/`tool`/token attributes rather than generic call metadata.

## Next Module

**Evaluation** (`ai_platform/evaluation/`, new). Tracing now answers "what happened on a request" — the natural next question is "was the answer any good." Evaluation is where the platform gets a harness for running a set of test cases (a prompt plus an expected property of the response) through `RuntimeEngine` and scoring the results, either with deterministic checks or an LLM-as-judge. It's the first module that treats Runtime itself as the system under test rather than a dependency to compose against, and it can now use `Tracer`/`Span` data (token usage, latency, tool-call counts) as inputs to what "quality" means beyond just the final text.
