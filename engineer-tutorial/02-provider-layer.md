# 02 — The Provider Abstraction Module

*Internal onboarding doc — AI Platform, Provider component (`ai_platform/providers/`)*

## 1. Executive Summary

The Provider layer is the platform's boundary against LLM vendors. It gives Runtime (and anything else that needs a model completion) one call signature — `ModelProvider.complete(messages, model=...)` — regardless of which vendor actually answers it. Today there is exactly one implementation, `AnthropicProvider`, wrapping the Anthropic SDK. It sits one layer below Runtime in the dependency chain: the Gateway depends on `RuntimeClient`; Runtime (once built) will depend on `ModelProvider` the same way.

Scope decision for this pass: **Claude only.** The interface is shaped so a second provider is a new class, not a rewrite — but only one is built now, deliberately, to avoid designing an abstraction against a single data point.

## 2. The Problem

Every LLM vendor SDK has its own request/response shape, its own exception hierarchy, and its own quirks. Anthropic's Messages API, concretely: `system` is a *separate top-level parameter*, not a message with `role="system"` inside the array — which is how our platform's generic `ChatMessage` (and OpenAI-style APIs) model it. If Runtime called `anthropic.AsyncAnthropic().messages.create(...)` directly, it would need to:

- know this system-message quirk,
- catch `anthropic.AuthenticationError`, `anthropic.RateLimitError`, `anthropic.APITimeoutError` by name,
- and re-derive usage/stop-reason extraction from the SDK's response object,

every place it needs a completion. Add a second provider later, and all of that logic forks in two, or Runtime grows an if/else on vendor.

## 3. Motivation

This is the same motivation as the Gateway/RuntimeClient split, one layer deeper: separate "how do we talk to *a* model" (Provider) from "what do we do with the model's answer" (Runtime). Once the interface exists, Runtime's logic — building the message list, deciding which tools to expose, deciding when to stop — never has to change based on which vendor is answering. Vendor-specific translation is quarantined in exactly one file per vendor.

**Alternative considered:** skip the abstraction, call the Anthropic SDK directly wherever a completion is needed (i.e., inside Runtime once it exists). Rejected — the first time this platform needs a second provider (fallback on outage, cost-based routing, A/B testing model quality), every call site would need rewriting instead of registering one new class.

## 4. Responsibilities

**Provider Abstraction should:**
- Define one interface (`ModelProvider`) that Runtime programs against
- Translate the platform's generic `ChatMessage` list into each vendor's actual wire format
- Translate each vendor's response back into a generic `ProviderResponse` (message + usage + stop reason)
- Translate each vendor's SDK exceptions into the platform's transport-agnostic `ProviderError` hierarchy (`common/errors.py`), so nothing above it ever catches an `anthropic.*` exception by name

**Provider Abstraction should NOT:**
- Decide *which* provider/model to use for a given request — that's a Runtime/routing decision
- Know about tools, memory, or conversation history — it takes exactly the messages it's given
- Retry failed calls or implement fallback logic — that's an orchestration concern, one layer up
- Talk HTTP or know it's ultimately serving a Gateway request — it's a plain async Python interface, callable from a script, a test, or Runtime identically

## 5. Architecture

```
              Runtime (future)
                    │  ModelProvider.complete(messages, model=...)
                    ▼
      ┌─────────────────────────────┐
      │   ModelProvider (Protocol)     │   ai_platform/providers/interfaces.py
      └──────────────┬────────────────┘
                      │ implemented by
                      ▼
      ┌─────────────────────────────┐
      │   AnthropicProvider            │   ai_platform/providers/anthropic_provider.py
      │                                 │
      │   • splits system vs. turns      │
      │   • calls client.messages.create  │
      │   • maps SDK exceptions →         │
      │     ProviderError hierarchy        │
      └──────────────┬────────────────┘
                      │  AsyncAnthropic (injected)
                      ▼
                Claude API
```

Upstream: Runtime will hold a `ModelProvider` reference exactly the way the Gateway holds a `RuntimeClient` reference today (same DI pattern, one level down). Downstream: the Anthropic SDK's `AsyncAnthropic` client, injected into `AnthropicProvider.__init__` rather than constructed inside it — the same reason the client is injected: tests supply a fake, `create_anthropic_provider(settings)` supplies the real one in production.

## 6. Request Flow

Walking through `AnthropicProvider.complete(messages, model="claude-sonnet-5")`:

1. **Caller** (Runtime, or a test) passes a `list[ChatMessage]` — the same generic type the Gateway already uses.
2. **System extraction**: messages with `role == "system"` are pulled out and joined into a single string; everything else becomes a `{"role": ..., "content": ...}` turn. This is the one piece of Anthropic-specific knowledge in the whole platform.
3. **SDK call**: `self._client.messages.create(model=model, max_tokens=max_tokens, system=system_prompt or None, messages=turns)`.
4. **On success**: the response's text content blocks are concatenated, and `stop_reason` / `usage.input_tokens` / `usage.output_tokens` are read off the SDK's response object and repackaged into a `ProviderResponse`.
5. **On failure**: the SDK raises one of its own exception types (`AuthenticationError`, `RateLimitError`, `APITimeoutError`, or the base `APIStatusError`). Each is caught and re-raised as the matching platform exception (`ProviderAuthError`, `ProviderRateLimitError`, `ProviderTimeoutError`, `ProviderError`) — the caller never sees an `anthropic.*` type.
6. **Return**: a `ProviderResponse`, a plain pydantic model with no vendor-specific shape left in it.

## 7. Design Decisions

**Why a `Protocol` (`ModelProvider`) instead of an ABC?** Consistent with `RuntimeClient` in the Gateway module — structural typing means a test fake doesn't need to inherit from anything, it just needs the right method signature. Kept the same pattern platform-wide rather than mixing ABCs and Protocols.

**Why is the Anthropic SDK client injected into `__init__` rather than constructed inside `AnthropicProvider`?** Testability without cost or flakiness: real Claude calls cost money, need network access and a real API key, and are non-deterministic. Injecting the client means tests substitute a `FakeAnthropicClient` (`tests/providers/conftest.py`) that returns canned responses or raises canned SDK exceptions — the *translation logic* is what's under test, not Anthropic's actual API. Production code gets the real client via a small factory, `create_anthropic_provider(settings)`, which is the one place `AsyncAnthropic(api_key=...)` is actually constructed.

**Why does `ProviderResponse` carry `input_tokens`/`output_tokens`/`stop_reason`, when the Gateway's `ChatResponse` doesn't expose any of that?** Different consumers, different needs. `ChatResponse` is the *external* API contract the Gateway promises to clients — deliberately minimal. `ProviderResponse` is *internal*, and Runtime will need token counts for cost accounting/audit logging (an enterprise requirement named early in this project) even if the Gateway chooses not to expose them to callers today. Keeping usage data at the Provider layer means it's available the moment Runtime wants it, without changing this module.

**Why do `ProviderError` and its subclasses live in `common/errors.py` instead of a new `providers/errors.py`?** They extend `PlatformError`, the same base every other platform exception extends — and the Gateway's exception handler (`api/errors.py`) already knows how to map any `PlatformError` subclass to an HTTP status by type. Adding them to `common/errors.py` means that mapping (`ProviderAuthError → 500`, `ProviderRateLimitError → 503`, `ProviderTimeoutError → 504`, generic `ProviderError → 502`) is already wired up in `api/errors.py`, ready for the day Runtime starts raising them — even though nothing calls this module through the Gateway yet.

## 8. Alternative Designs

| Alternative | Why not |
|---|---|
| **Call `anthropic` SDK directly from Runtime, no abstraction** | Rejected — Runtime would own vendor-specific translation and exception handling, and every future provider would mean editing Runtime instead of adding a class. |
| **One `ModelProvider` implementation that branches internally on a `provider` string ("anthropic" vs "openai")** | A form of the abstraction, but as a single class with conditionals rather than polymorphism. Rejected — violates single responsibility (one class knowing two vendors' quirks) and makes testing one vendor in isolation harder. |
| **Wrap a third-party unification library (e.g. LiteLLM) instead of writing our own thin layer** | A legitimate production choice — LiteLLM already solves multi-provider translation. Rejected *for this exercise* because the point is to understand and own the abstraction boundary, not depend on someone else's. Worth revisiting once a second/third provider is actually needed and the translation logic starts feeling like undifferentiated plumbing. |
| **Return the raw SDK response type instead of a generic `ProviderResponse`** | Rejected — leaks Anthropic's response shape into Runtime, defeating the entire point of the abstraction. |

## 9. Trade-offs

**Gained:** Runtime will never import `anthropic` directly; adding a second provider is additive (`OpenAIProvider` implementing the same `ModelProvider`), not a rewrite; provider failures are testable without hitting a real API or spending money.

**Cost:** one more indirection layer between "I want a completion" and the actual API call; the generic `ProviderResponse`/`ChatMessage` types necessarily can't represent every vendor-specific feature (e.g. Anthropic's extended thinking blocks, or vendor-specific tool-use formats) — the abstraction will need to grow deliberately as those features become required, not be over-built for them now.

## 10. Production Evolution

```
v0.1 (this module)
  one provider (Anthropic), client injected
  no retry, no fallback, no streaming
        │
        ▼
v0.2
  streaming support (async generator variant of complete())
  timeout/retry policy at the provider layer (or explicitly deferred
    to Runtime — a decision to make deliberately, not by default)
        │
        ▼
Enterprise version
  second provider (OpenAI) added, proving the abstraction
  provider selection/routing logic in Runtime (cost-based, fallback-on-error)
  per-provider cost tracking feeding into the future audit-logging module
  circuit breaker around a provider that's degrading
        │
        ▼
Large-scale platform
  provider layer becomes a routing mesh: multiple API keys per
    provider for quota pooling, region-aware routing, canary rollout
    of new model versions
  cost/latency-based automatic provider selection per request
```

The scaling challenge here isn't state (unlike the Gateway's rate limiter) — it's *feature parity*. Every provider added has to fit through the same `ModelProvider.complete()` signature, and the generic types (`ChatMessage`, `ProviderResponse`) have to be extended carefully so they stay meaningful across vendors instead of becoming a lowest-common-denominator that hides useful vendor features, or a leaky union that defeats the abstraction.

## 11. Real-world Examples

- **LiteLLM** — the production-grade version of exactly this module: one `completion()` call signature across 100+ providers, with the same system-message-translation problem solved once per provider adapter internally.
- **LangChain's `BaseChatModel`** — the same Protocol-like interface pattern: every provider (Anthropic, OpenAI, ...) implements the same base class so chains don't need to know which model they're calling.
- **OpenAI Agents SDK** — its `Model` interface plays the same role: an abstraction point so an "Agent" doesn't hardcode a vendor.
- **Vercel AI SDK** — `generateText({ model })` uses a provider-registry pattern; swapping `anthropic('claude-...')` for `openai('gpt-...')` is a one-line change specifically because of an abstraction like this one.

## 12. Common Mistakes

- **Letting the SDK's exception types leak past the provider boundary.** If Runtime ever has to `except anthropic.RateLimitError`, the abstraction has failed — it should only ever see `ProviderError` subclasses. This module catches every SDK-raised exception path explicitly, in the narrowest-to-widest order (`AuthenticationError`/`RateLimitError` before the base `APIStatusError`, since they're subclasses of it).
- **Constructing the SDK client inside the class instead of injecting it.** Makes unit testing require a real API key/network, or heavy mocking of the SDK's internals rather than a small fake object with one method.
- **Designing the generic response type around the first provider's exact fields.** `ProviderResponse` was written thinking about what any provider (present or future) plausibly reports — message, usage, stop reason — not merely a copy of Anthropic's `Message` fields.
- **Forgetting that `system` in Anthropic's API is not a message.** A naive port of an OpenAI-style message list straight into `messages.create()` would either error or silently be ignored depending on the SDK version — this is exactly the kind of vendor quirk the Provider layer exists to absorb once, in one place.

## 13. Best Practices

- Inject third-party SDK clients; never construct them inside the class that uses them.
- Catch vendor SDK exceptions at the narrowest boundary and re-raise as your own transport-agnostic types immediately — don't let them travel.
- Keep the generic request/response types intentionally minimal until a second implementation proves what actually needs to be generic.
- Reuse the platform's existing exception hierarchy (`common/errors.py`) instead of inventing a parallel one per module — it's what lets the Gateway's error mapping stay a single, complete table.

## 14. Knowledge

**Must Know**
- Why an adapter/facade pattern is used to unify multiple third-party APIs behind one interface.
- The difference between structural typing (`Protocol`) and nominal typing (`ABC`) in Python, and when each is preferable.
- Dependency injection for testability: why injecting an SDK client instead of constructing it internally matters for unit testing.

**Good to Know**
- Anthropic's Messages API shape vs. OpenAI's Chat Completions shape (system-as-parameter vs. system-as-message) — a real, common source of bugs when porting code between the two.
- Exception hierarchy design: catching subclasses before base classes, and why `AuthenticationError`/`RateLimitError` must be caught before the more general `APIStatusError` they inherit from.
- Token usage accounting (`input_tokens`/`output_tokens`) as the basis for cost tracking in any production LLM system.

**Advanced**
- Multi-provider routing strategies: cost-based, latency-based, and fallback-on-error routing, and where that logic belongs (Runtime, not Provider).
- Streaming response design: how `complete()` would need to change shape (return an async generator, not a single `ProviderResponse`) to support token-by-token output, and how that ripples up through Runtime and the Gateway.
- Circuit breaker patterns for a provider that's degrading, to avoid cascading latency into every request.

## 15. Key Takeaways

1. The Provider layer exists to quarantine vendor-specific translation (message shape, exceptions, response parsing) in exactly one place per vendor, so Runtime never has to know which model it's actually calling.
2. `ModelProvider` is a `Protocol`, matching the same interface pattern already used for `RuntimeClient` in the Gateway — one consistent DI pattern across the platform.
3. The Anthropic SDK client is injected, not constructed internally — this is what makes the translation logic (system-message extraction, exception mapping) fully unit-testable without a real API key or network call.
4. `ProviderError` subclasses live on the platform's existing `PlatformError` hierarchy, so the Gateway's error-to-HTTP mapping already covers them, ready for when Runtime starts raising them.
5. Scope was deliberately kept to one provider — designing a "multi-provider abstraction" against a single real implementation risks guessing wrong about what's actually generic versus Anthropic-specific.

## Further Reading

1. Anthropic — [Messages API reference](https://docs.anthropic.com/en/api/messages) (official docs) — the actual wire format this module translates to/from.
2. LiteLLM — [Provider docs](https://docs.litellm.ai/docs/providers) — the production-scale version of this exact abstraction across 100+ vendors.
3. PEP 544 — [Protocols: Structural subtyping](https://peps.python.org/pep-0544/) — same reference as the Gateway tutorial, applied here to `ModelProvider`.
4. Anthropic Python SDK — [source, exception hierarchy](https://github.com/anthropics/anthropic-sdk-python) — worth reading `_exceptions.py` directly to see the full `APIStatusError` subclass tree this module catches against.
5. Vercel AI SDK — [Provider architecture](https://sdk.vercel.ai/docs/foundations/providers-and-models) — a well-documented multi-provider abstraction in a different language ecosystem, useful for comparison.

## Next Module

**Runtime** (`ai_platform/runtime/`, replacing the current `EchoRuntimeClient` stub). With both `RuntimeClient` (Gateway-facing) and `ModelProvider` (now real, not stubbed) in place, Runtime is where they finally compose: take a validated `ChatRequest` from the Gateway, decide what to send the model (system prompt, conversation so far), call `ModelProvider.complete()`, and shape the result back into the `ChatResponse` the Gateway promised. This is also the natural point to introduce the Tool Registry's first real consumer, once tool-calling is in scope.
