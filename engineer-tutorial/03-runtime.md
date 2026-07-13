# 03 — The Runtime Module

*Internal onboarding doc — AI Platform, Runtime component (`ai_platform/runtime/`)*

## 1. Executive Summary

Runtime is where the platform's two interfaces finally compose. The Gateway holds a `RuntimeClient` reference; the Provider layer exposes a `ModelProvider`. Until this module existed, nothing sat between them — the Gateway called `EchoRuntimeClient`, a placeholder that echoed the last message back with no model call at all.

`RuntimeEngine` is the first real `RuntimeClient`: it takes a validated `ChatRequest` from the Gateway, calls `ModelProvider.complete()`, and shapes the result into the `ChatResponse` the Gateway already promises callers. In this pass it is deliberately thin — no tool loop, no memory, no retries — because those are separate architectural decisions that deserve their own module, not defaults smuggled into the first working version.

## 2. The Problem

The Gateway and Provider modules were both built to depend on interfaces, specifically so each could be developed independently. But an interface with no real implementation on the other side is only half a system — `EchoRuntimeClient` proved the Gateway's plumbing worked, and `AnthropicProvider` proved a real model call worked, but nothing yet decided *what to send the model* or *how to turn its answer into the Gateway's response shape*.

Without Runtime, that decision has exactly two bad places to live:
- Inside the Gateway's route handler — which then needs to import the Provider layer directly, collapsing the Gateway/Runtime boundary the first module was built to establish.
- Inside the Provider itself — which then needs to know about request-level concerns (which model, what system prompt, later: which tools) that have nothing to do with talking to Anthropic's wire format.

Either choice re-couples two things that were kept apart on purpose.

## 3. Motivation

This is the same split as Gateway/Provider, one layer inward: separate "how do we talk to *a* model" (Provider) from "what do we do with the model's answer, and what do we send it in the first place" (Runtime). Provider owns mechanics; Runtime owns policy. Once Runtime exists as its own module, it can grow — tool-calling loops, conversation memory, multi-provider routing — without the Gateway or the Provider layer ever needing to change, because both already depend on Runtime only through an interface (`RuntimeClient`) or something Runtime itself depends on (`ModelProvider`).

## 4. Responsibilities

**Runtime should:**
- Implement `RuntimeClient` (`common/interfaces.py`) so the Gateway can call it exactly like it called the stub
- Decide what to hand the model — today: pass `ChatRequest.messages` and `ChatRequest.model` straight through; later: system prompt construction, tool definitions, conversation history assembly
- Call `ModelProvider.complete()` and translate its `ProviderResponse` into the Gateway's `ChatResponse`
- Compose future modules (Tool Registry, Memory) once they exist — Runtime is the natural place they plug into, not the Gateway or the Provider layer

**Runtime should NOT:**
- Know about HTTP, headers, or auth — that's the Gateway's job, and `RuntimeEngine` has no FastAPI import anywhere in it
- Know which vendor is answering, or how to translate `ChatMessage` into that vendor's wire format — that's the Provider's job
- Catch and re-wrap `ProviderError` — it's already on the platform's shared `PlatformError` hierarchy and the Gateway's error handler already maps it to HTTP by type; re-wrapping it would throw away information for no benefit
- Retry failed provider calls in this version — a real decision, deferred deliberately (see Production Evolution), not an oversight

## 5. Architecture

```
                 Gateway
                    │  RuntimeClient.handle_chat(request)  (interface)
                    ▼
      ┌─────────────────────────────┐
      │   RuntimeEngine                │   ai_platform/runtime/engine.py
      │                                 │
      │   • holds a ModelProvider        │
      │   • passes messages/model through │
      │   • shapes ProviderResponse       │
      │     into ChatResponse              │
      └──────────────┬────────────────┘
                      │  ModelProvider.complete(messages, model=...)  (interface)
                      ▼
      ┌─────────────────────────────┐
      │   AnthropicProvider            │   ai_platform/providers/anthropic_provider.py
      └─────────────────────────────┘
```

Upstream: the Gateway holds `RuntimeEngine` only through the `RuntimeClient` Protocol — `api/dependencies.py::get_runtime_client()` is still the single place that wires the concrete type, exactly as it was when it wired the stub. Downstream: `RuntimeEngine` holds a `ModelProvider` reference, injected at construction (`create_anthropic_provider(get_settings())`), not imported directly — the same injection pattern the Provider layer used for its own SDK client.

## 6. Request Flow

Walking through `POST /v1/chat` from where the Gateway tutorial left off:

1. **Gateway route handler** (`routes/chat.py`) has a validated `ChatRequest` and an injected `RuntimeClient` from `get_runtime_client()` — today, a real `RuntimeEngine`.
2. **`await runtime.handle_chat(request)`** calls into `RuntimeEngine.handle_chat`.
3. **`RuntimeEngine`** calls `self._provider.complete(request.messages, model=request.model)` — no transformation of the message list happens here; that's the Provider's job (splitting out `system`, for instance).
4. **`AnthropicProvider.complete()`** does its own translation and calls Claude, returning a `ProviderResponse` (message + usage + stop reason) or raising a `ProviderError` subclass.
5. **On success**, `RuntimeEngine` takes `ProviderResponse.message` and wraps it into a `ChatResponse` alongside the requested model name — usage/stop-reason data is available on `ProviderResponse` but the Gateway's external contract doesn't expose it (yet).
6. **On failure**, the `ProviderError` raised in step 4 is not caught anywhere in Runtime — it propagates unchanged up through the route handler to the Gateway's registered exception handler (`api/errors.py`), which already maps `ProviderAuthError`/`ProviderRateLimitError`/`ProviderTimeoutError`/`ProviderError` to HTTP status codes.
7. **Response** is serialized and returned exactly as it would have been from the old stub — the Gateway's external contract hasn't changed at all.

## 7. Design Decisions

**Why does `RuntimeEngine` not catch `ProviderError`?**
It's tempting to wrap every provider failure in something like `RuntimeUnavailableError` "because Runtime is the layer talking to the model." But `ProviderError` already lives on `PlatformError` (`common/errors.py`), and `api/errors.py` already has a complete, type-based mapping for its subclasses — mapping that was written *before* Runtime existed, specifically so it would be ready the day Runtime started raising them. Catching and re-wrapping here would throw away the distinction between "the provider rejected our credentials" (500) and "the provider rate-limited us" (503) unless Runtime re-implemented the same mapping — duplicated logic for no gain.

**Why is `RuntimeEngine.handle_chat` a straight pass-through in this version, with no system-prompt injection or history assembly?**
Because there's exactly one caller and one provider today, and no requirement yet for what a "system prompt policy" should look like. Building that logic now would be guessing at a shape with a single data point — the same reasoning that kept the Provider layer to one vendor. It's an intentional gap, called out explicitly in Production Evolution below, not a missing feature.

**Why is the `ModelProvider` injected into `RuntimeEngine.__init__` rather than constructed inside it?**
Same testability reasoning as `AnthropicProvider`'s injected SDK client: tests substitute a `FakeModelProvider` (`tests/runtime/conftest.py`) that returns canned `ProviderResponse`s or raises canned `ProviderError`s, so `RuntimeEngine`'s composition logic is unit-tested without a real API key, network access, or cost.

**Why does `tests/api/conftest.py` now need its own `FakeRuntimeClient`, when it didn't before?**
Before this module, `get_runtime_client()` returned `EchoRuntimeClient` — itself already a safe, network-free fake, so Gateway tests could use the real dependency wiring unmodified. Now that `get_runtime_client()` wires a real `RuntimeEngine` backed by `AnthropicProvider`, using the real wiring in Gateway tests would mean every auth/rate-limit test makes an actual (and costly, flaky, key-dependent) call to Claude. The fix is the standard FastAPI pattern: `app.dependency_overrides[get_runtime_client] = FakeRuntimeClient` in the `client` fixture, so Gateway tests keep exercising real auth/rate-limit/routing logic against a fake Runtime, while `RuntimeEngine` itself is tested for real in `tests/runtime/`.

## 8. Alternative Designs

| Alternative | Why not |
|---|---|
| **Gateway calls `ModelProvider` directly, no Runtime module** | Collapses two different concerns (HTTP/quota vs. orchestration policy) back into one layer, and means the Gateway must import the Provider layer directly — exactly the coupling the `RuntimeClient` interface was introduced to prevent. |
| **`RuntimeEngine` catches `ProviderError` and re-raises as `RuntimeUnavailableError`** | Seems like better encapsulation ("Runtime shouldn't leak Provider's exception types") but `ProviderError` is already transport-agnostic and already mapped by the Gateway — re-wrapping loses status-code granularity (auth failure vs. rate limit vs. timeout all becoming one generic "unavailable") unless the wrapping logic duplicates the existing mapping table. |
| **Build tool-calling and system-prompt policy into this pass, since Runtime is "supposed to" do that eventually** | Rejected for the same reason the Provider layer stayed single-vendor: one real request pattern isn't enough evidence to design a tool loop or prompt-templating scheme against. Ship the composition first, add policy once there's a concrete requirement driving its shape. |
| **A registry/factory that picks which `ModelProvider` to use per-request inside `RuntimeEngine`** | Legitimate future design (model routing, fallback-on-error) but premature with exactly one provider — would be an abstraction over a single implementation, the same anti-pattern flagged in the Provider tutorial. |

## 9. Trade-offs

**Gained:** the Gateway now serves real model completions end to end, with zero changes to Gateway code — only `get_runtime_client()` changed, proving the interface boundaries from modules 01 and 02 actually pay off. `RuntimeEngine`'s composition logic is fully unit-testable via `FakeModelProvider`, independent of both the Gateway and any real provider.

**Cost:** the module is currently thin enough that its value is entirely architectural, not behavioral — `RuntimeEngine` today does barely more than the stub it replaced. That's a deliberate, temporary state: the payoff shows up as tool-calling, memory, and multi-provider routing get added here instead of forcing a rewrite of the Gateway or Provider layers.

## 10. Production Evolution

```
v0.1 (this module)
  single ModelProvider, injected
  pass-through: messages/model in, message out
  no retries, no tool loop, no memory
        │
        ▼
v0.2
  system-prompt policy (platform-level system prompt,
    merged with or overriding caller-supplied system messages)
  retry/backoff policy for transient ProviderError subtypes
    (a decision made here, explicitly, not deferred silently)
  Tool Registry's first consumer: a tool-calling loop
    (call provider → inspect for tool_use → execute tool → re-call)
        │
        ▼
Enterprise version
  conversation memory (multi-turn history assembly, summarization
    for long conversations)
  cost/usage accounting fed by ProviderResponse.input_tokens/output_tokens
    into an audit-logging module
  multi-provider routing (cost-based, latency-based, fallback-on-outage)
  guardrails/policy checks before and after the model call
        │
        ▼
Large-scale platform
  streaming responses (async generator variant of handle_chat)
  per-tenant model/tool allowlists enforced at this layer
  distributed tracing spans around every provider call
  circuit breaker around a degrading provider, informing routing
```

The scaling challenge here is scope, not state (unlike the Gateway's rate limiter): each version adds a genuinely new capability — tool loop, memory, routing — rather than moving existing state somewhere distributed. The risk to manage deliberately is complexity creep inside `RuntimeEngine` itself; as responsibilities like "tool execution" and "memory assembly" grow, they should become their own collaborator classes that `RuntimeEngine` composes, not logic inlined into one growing `handle_chat` method.

## 11. Real-world Examples

- **LangGraph** — a graph of nodes (model call, tool call, conditional edges) plays exactly Runtime's role: the orchestration layer between an API surface and a model provider abstraction.
- **OpenAI Agents SDK** — its `Agent`/`Runner` loop is a more fully-built version of what `RuntimeEngine` will grow into: decide what to send the model, inspect the response for tool calls, loop until done.
- **LlamaIndex** — its query engines and agent runners occupy the same "orchestration between API and model" position, composing a retriever/tool layer with a model abstraction.
- **Dify** — its workflow/agent execution engine sits between the App API (Gateway-equivalent) and model providers, the same boundary this module establishes.

## 12. Common Mistakes

- **Letting Runtime import a vendor SDK directly "just this once."** Defeats the entire Provider abstraction — the whole point of `ModelProvider` is that `RuntimeEngine` never needs to know Anthropic exists.
- **Re-wrapping `ProviderError` in a new exception type "for cleanliness."** Loses the granular HTTP mapping the Gateway already has, unless the wrapping is careful to preserve it — usually easier to just let it propagate.
- **Building tool-calling, memory, and streaming all in the first version of Runtime.** Each is a real architectural decision (how does a tool loop terminate? how is history truncated? how does streaming change the `RuntimeClient` interface?) — bundling them into the first pass means guessing at all of them at once with no real usage to validate any of them.
- **Testing `RuntimeEngine` against the real `AnthropicProvider` instead of a fake.** Makes Runtime's own tests slow, costly, and dependent on network/API-key availability, when the composition logic under test has nothing to do with Anthropic specifically.

## 13. Best Practices

- Compose interfaces, don't reach past them — `RuntimeEngine` depends on `ModelProvider`, never on `AnthropicProvider` or `anthropic` directly.
- Let already-classified errors propagate unchanged across a module boundary instead of re-wrapping them, when the receiving boundary already knows how to handle them.
- Ship the thinnest correct composition first; let real requirements (not anticipated ones) drive when tool-calling, memory, or routing get added.
- Keep the module under test with fakes at every layer (`FakeModelProvider` here, `FakeRuntimeClient` at the Gateway) so each module's tests fail only for reasons that module actually owns.

## 14. Interview Knowledge

**Must Know**
- Why an orchestration layer (Runtime) is kept separate from both the API boundary (Gateway) and the vendor abstraction (Provider) — three different concerns, three different reasons to change.
- Composition over inheritance: `RuntimeEngine` *has a* `ModelProvider`, it doesn't *is a* provider or subclass one.
- Why letting a well-typed exception propagate across a module boundary is often better than catching and re-wrapping it.

**Good to Know**
- The shape a tool-calling loop takes conceptually: call model → inspect response for a tool-use request → execute the tool → feed the result back to the model → repeat until a final answer.
- Why test doubles should be scoped per-module (`FakeModelProvider` for Runtime's own tests, `FakeRuntimeClient` for the Gateway's) rather than one shared mega-fake — each module's tests should only fail for reasons that module owns.

**Advanced**
- Designing `handle_chat` to support streaming without breaking the `RuntimeClient` interface for non-streaming callers (e.g., a separate method, or a request flag changing the return type to an async generator).
- Where conversation memory should live: inside Runtime (stateless per-call, history passed in) vs. a separate Memory module Runtime queries — a real architectural fork point.
- Multi-provider routing strategies (cost-based, latency-based, fallback-on-error) and why that decision belongs in Runtime, composing multiple `ModelProvider`s, rather than in the Provider layer itself.

## 15. Key Takeaways

1. Runtime is where the Gateway's `RuntimeClient` interface and the Provider layer's `ModelProvider` interface finally compose — it owns policy (what to send, what to do with the answer), not mechanics (how to talk to a vendor) or transport (HTTP, auth, quota).
2. `RuntimeEngine` holds its `ModelProvider` via constructor injection, the same DI pattern used everywhere else in the platform, purely for testability with a fake.
3. `ProviderError` is deliberately left to propagate unchanged rather than being re-wrapped — it's already on the shared `PlatformError` hierarchy, and the Gateway's error mapping already knows how to handle it.
4. This version is intentionally thin — pass-through messages, no tool loop, no memory, no retries — because those are separate, real architectural decisions that deserve their own evidence-driven design, not defaults baked into the first working version.
5. Swapping the stub for the real `RuntimeEngine` required zero changes to the Gateway's routes or the Provider's implementation — direct proof that the interface boundaries established in modules 01 and 02 did their job.

## Further Reading

1. OpenAI Agents SDK — [Agent loop / Runner concept](https://platform.openai.com/docs/guides/agents) — the fuller version of what `RuntimeEngine` composes toward.
2. LangGraph — [Core concepts](https://langchain-ai.github.io/langgraph/concepts/low_level/) — graph-based orchestration as a generalization of a Runtime module.
3. Gang of Four — Composition over Inheritance principle — the reasoning behind `RuntimeEngine` holding a `ModelProvider` rather than extending one.
4. Anthropic — [Tool use / function calling guide](https://docs.anthropic.com/en/docs/build-with-claude/tool-use) — the mechanics the v0.2 tool-calling loop will need.
5. Martin Fowler — [Inversion of Control Containers and the Dependency Injection pattern](https://martinfowler.com/articles/injection.html) — background for why constructor injection was used again here.

## Next Module

**Tool Registry** (`ai_platform/tools/`, new). Runtime can now produce a real model completion but has no way to give the model capabilities beyond its own text generation. The Tool Registry defines what a "tool" is platform-wide (name, schema, execution function), and Runtime becomes its first consumer: inspecting a `ProviderResponse` for a tool-use request, looking the tool up in the registry, executing it, and feeding the result back to the model. This is also the point where `ChatMessage`/`ProviderResponse` will need deliberate extension to carry tool-call data — the first real test of how far the generic types can stretch before they need to grow.
