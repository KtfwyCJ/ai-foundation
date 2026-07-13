# 05 — The Memory Module

*Internal onboarding doc — AI Platform, Memory component (`ai_platform/memory/`)*

## 1. Executive Summary

Memory is what turns a series of stateless requests into an actual conversation. Every module before this one — Gateway, Provider, Runtime, Tool Registry — treated each `POST /v1/chat` as a self-contained transaction: the caller sends the full message list, the platform produces one reply, and nothing is remembered afterward. That was a correct, deliberate simplification for four modules running in sequence, but it means today a client wanting a multi-turn conversation has to store and resend the *entire* history — including any tool-use/tool-result exchange from every prior turn — on every single call.

`MemoryStore` gives Runtime somewhere to put that history between requests, addressed by a caller-supplied `conversation_id`. `InMemoryStore` is the first (and, deliberately, only) implementation: a dict-backed, single-process store that proves the interface works before anyone reaches for Redis or a database.

## 2. The Problem

Two different things break down without server-side conversation state:

- **Client burden.** Every caller of the Gateway has to maintain its own copy of the full conversation and replay it on each request — including the exact `tool_use`/`tool_result` block structure the Tool Registry module introduced, which most simple clients have no reason to want to manage themselves.
- **No durable place for a conversation to live.** The tool-calling loop inside `RuntimeEngine.handle_chat` already builds up a rich `messages` list across iterations — but that list exists only in local Python memory for the duration of one request. The moment `handle_chat` returns, it's gone. There was no way to say "continue this conversation" across two separate HTTP calls at all.

Skipping this module doesn't remove the need for conversation state — it just pushes the problem onto every client integrating with the platform, each of which would solve it slightly differently (or not at all).

## 3. Motivation

Same separation-of-concerns instinct behind every prior module: keep "what has been said in this conversation" (state) apart from "what do we do about it" (policy). `MemoryStore` is a Protocol, `InMemoryStore` is today's only implementation, and `RuntimeEngine` depends on the interface — the identical DI shape already used for `ModelProvider` and the `Tool` protocol. That means swapping in a Redis- or Postgres-backed store later is a new class plus a one-line change in `api/dependencies.py`, not a change to `RuntimeEngine`'s orchestration logic.

**Scope decision for this pass:** persistence across requests only, nothing about long-conversation compression. A conversation that grows past what fits in a model's context window needs a real strategy — truncate the oldest turns? Summarize them? Keep tool exchanges but drop old text? — and designing that now, with one implementation and no real usage data, would mean guessing. The same discipline that kept the Provider layer single-vendor and the tool loop's iteration cap a plain constant applies here: ship the simple, correct version, and let an actual long-conversation requirement drive the summarization design later.

## 4. Responsibilities

**Memory should:**
- Define the `MemoryStore` protocol (`interfaces.py`): `async load(conversation_id) -> list[ChatMessage]` and `async append(conversation_id, messages) -> None`
- Provide at least one working implementation (`in_memory.py::InMemoryStore`) so the interface is proven, not just declared
- Persist and return `ChatMessage`s exactly as given — no reshaping, no filtering, no compression

**Memory should NOT:**
- Decide *when* to load or persist — that's Runtime's job (`RuntimeEngine.handle_chat` calls `load` before the loop and `append` after it)
- Summarize, truncate, or otherwise edit history to fit a context window — a real, separate design decision, deliberately deferred
- Know anything about tools, providers, or HTTP — it stores and returns `ChatMessage` objects, the same transport-agnostic type used everywhere else in the platform
- Enforce who is allowed to read or write a given `conversation_id` — today, whoever holds the id can access that conversation's history; a future Auth/RBAC concern, same boundary already flagged for tool access in module 04

## 5. Architecture

```
                 RuntimeEngine
                    │  memory.load(conversation_id)         (before the tool loop)
                    │  memory.append(conversation_id, new_messages)  (after it resolves)
                    ▼
      ┌─────────────────────────────┐
      │   MemoryStore (Protocol)       │   ai_platform/memory/interfaces.py
      └──────────────┬────────────────┘
                      │ implemented by
                      ▼
      ┌─────────────────────────────┐
      │   InMemoryStore                │   ai_platform/memory/in_memory.py
      │   { conversation_id: [msgs] }    │
      └─────────────────────────────┘
```

Upstream: `RuntimeEngine` holds a `MemoryStore` reference, constructor-injected exactly like its `ModelProvider` and `ToolRegistry` — the third instance of the same DI pattern in this platform. Downstream: `InMemoryStore` is a leaf; it stores plain `ChatMessage` objects and calls nothing else. Sideways: Memory doesn't interact with the Tool Registry or Provider layer directly — it only ever sees whatever `ChatMessage` list `RuntimeEngine` hands it, whether that list came from a plain chat turn or a full tool-calling exchange.

## 6. Request Flow

Walking through two requests in the same conversation:

**Request 1** — `POST /v1/chat` with `conversation_id: "conv-1"`, no prior history:
1. `RuntimeEngine.handle_chat` sees `request.conversation_id` is set and a `MemoryStore` is present, so it calls `await self._memory.load("conv-1")` — returns `[]`, nothing stored yet.
2. `messages = history + list(request.messages)` — just this request's message(s), since history was empty.
3. The existing tool-calling loop runs unchanged (see module 04) — possibly zero, one, or several tool exchanges.
4. Once the model returns a final, non-tool-call answer, `RuntimeEngine` appends that reply to `messages`, then calls `_persist_new_turns`, which does `await self._memory.append("conv-1", messages[len(history):])` — i.e., everything added *since* the loaded history: the caller's new message(s), any tool exchange turns, and the final reply.
5. `ChatResponse` returns to the Gateway exactly as before — the Gateway's external contract hasn't changed.

**Request 2** — same `conversation_id`, a follow-up question:
1. `load("conv-1")` now returns everything persisted in request 1.
2. `messages = history + list(request.messages)` — the full prior exchange, followed by this new turn.
3. The model sees the entire conversation, including any earlier tool call and its result, without the caller having resent any of it.
4. The loop runs, resolves, and `_persist_new_turns` appends only *this* request's new turns on top of what was already stored — the store now holds the full, growing conversation.

**If the tool loop never converges** (`RuntimeToolLoopExceededError`, module 04's iteration cap), `_persist_new_turns` is never reached — nothing from a failed request is written to memory, so a conversation's stored history is only ever turns that actually produced a real reply.

## 7. Design Decisions

**Why is `conversation_id` optional on `ChatRequest` rather than mandatory?**
Making it optional means every caller and every test written before this module keeps working identically — `conversation_id=None` skips `load`/`append` entirely and `handle_chat` behaves exactly as it did in module 03. Memory is additive, not a breaking change to the chat contract, the same non-negotiable constraint every prior module was held to.

**Why does `_persist_new_turns` persist `messages[len(history):]` instead of the whole `messages` list?**
Because `history` (loaded at the start) is already in the store — re-appending it would duplicate every prior turn on every subsequent request. Slicing off exactly the loaded prefix means only what's genuinely new to this request gets written.

**Why is nothing persisted when the tool loop exceeds its iteration cap?**
`_persist_new_turns` is only called from the success path, right before returning a `ChatResponse` — `RuntimeToolLoopExceededError` is raised from inside the loop, before that call is ever reached. This is a deliberate consequence of the control flow, not an extra check: a conversation's stored history should only ever contain turns that produced a real answer, not a half-finished, failed tool exchange a client would have no way to make sense of on the next request.

**Why is `InMemoryStore` a plain dict with no locking, when the Gateway's rate limiter got the same "single-process" caveat but this doesn't even get a lock?**
The rate limiter's counter can be read-modified-written concurrently within one process by design (many requests, one shared quota). `InMemoryStore.append` is a `dict.setdefault(...).extend(...)` — safe for the realistic case of one conversation being driven by one caller at a time. Adding a lock now, with no evidence of concurrent-write conflicts on the same `conversation_id`, would be exactly the kind of unrequested robustness this platform has avoided everywhere else (no retry logic in the Provider layer, no tool-error recovery in the Tool Registry). It's named explicitly in Production Evolution below as a real gap once genuinely concurrent access to one conversation becomes a requirement.

**Why does `load()` return `list(self._conversations.get(conversation_id, []))` — a copy — instead of the stored list directly?**
So a caller mutating the returned list (as `RuntimeEngine` does immediately: `history + list(request.messages)`) can never accidentally corrupt the store's internal state. This is the same defensive-copy instinct that turned out to matter a great deal during this module's own development — see the next point.

**A real bug this module surfaced: recording a mutable list by reference instead of by value.**
While wiring persistence into `RuntimeEngine.handle_chat`, a line was added — `messages.append(result.message)` before persisting — that mutated the *same* `messages` list object already passed to `self._provider.complete(messages, ...)` on that iteration. `tests/runtime/conftest.py::FakeModelProvider` records each call's `messages` argument by reference (`self.calls.append({"messages": messages, ...})`), not a copy. Because it was the same list object, later appends retroactively "rewrote history" the fake had already recorded — tests asserting on "what was sent for call N" started failing, but only for iterations that happened *before* the final append, since the fake's stored reference now reflected the list's *final* state, not its state at call time. The fix: `self._provider.complete(list(messages), ...)` — passing a fresh shallow copy on every iteration, so each recorded call is an independent snapshot, immune to whatever `RuntimeEngine` does to its own working list afterward. The general lesson: a test double that stores a mutable argument by reference is only safe if the code under test never mutates that argument again — an assumption easy to satisfy by accident right up until it isn't.

## 8. Alternative Designs

| Alternative | Why not |
|---|---|
| **Require the client to resend full history every request (no Memory module at all)** | Pushes conversation-state management onto every integrator, and makes long tool-calling exchanges (module 04) especially painful to replay correctly from the client side. Rejected — this is the exact problem the module exists to solve. |
| **Store conversation state in `RuntimeEngine` instance attributes (e.g., a dict on `self`)** | Conflates Runtime's orchestration role with storage, and makes `RuntimeEngine` itself stateful and harder to test in isolation — every test would need to worry about state leaking between calls on a shared instance. A separate `MemoryStore` behind an interface keeps `RuntimeEngine` a thin composer, consistent with everything it delegates to `ModelProvider` and `ToolRegistry`. |
| **Skip the interface, hardcode `InMemoryStore` directly into `RuntimeEngine`** | Works today, but repeats the exact mistake every earlier module avoided (hardcoding `AnthropicProvider`, hardcoding tool dispatch) — the day a real backend (Redis, Postgres) is needed, `RuntimeEngine` would need editing instead of one wiring function changing. |
| **Auto-generate a `conversation_id` server-side if the caller doesn't supply one** | Would give every stateless caller accidental persistence they didn't ask for, and no way to retrieve it later without the platform inventing an out-of-band way to hand the id back. Rejected — persistence is opt-in, driven entirely by whether the caller supplies an id. |

## 9. Trade-offs

**Gained:** conversations can now span multiple HTTP requests without the client replaying history, including full tool-calling exchanges; `RuntimeEngine`'s persistence logic is fully unit-testable via `InMemoryStore` (a real, simple implementation, not even a fake needed); the interface is proven correct with a genuinely working store before anyone reaches for infrastructure like Redis.

**Cost:** `InMemoryStore` only works for a single process — restart the Gateway, and every conversation is gone; run more than one replica, and each one has its own disjoint memory, so which replica a request lands on now matters for correctness (a load balancer routing two requests in the same conversation to different replicas would see the second one as starting fresh). This is a known, explicitly documented v0.1 limitation, not an oversight — the same category of trade-off the Gateway's in-memory rate limiter already accepted.

## 10. Production Evolution

```
v0.1 (this module)
  single-process, dict-backed InMemoryStore
  no summarization/truncation of long conversations
  no expiration — conversations live as long as the process does
  no per-conversation access control
        │
        ▼
v0.2
  Redis-backed MemoryStore (shared across Gateway replicas)
  TTL/expiration policy for idle conversations
  a real strategy for long conversations: summarize old turns,
    or truncate to the last N turns plus a running summary
    (a decision made deliberately once a real conversation
    actually exceeds context, not guessed at now)
        │
        ▼
Enterprise version
  durable storage (Postgres/DynamoDB) for conversations that must
    survive infrastructure restarts, not just process restarts
  per-tenant conversation isolation and access control (who may
    load/append to a given conversation_id)
  audit trail of conversation history changes, feeding the same
    audit-logging module the Provider and Tool Registry tutorials
    both anticipated
        │
        ▼
Large-scale platform
  conversation sharding/partitioning across storage nodes
  semantic memory (embeddings-backed retrieval of relevant past
    context, not just literal turn-by-turn replay) layered on top
    of raw conversation storage
  cross-conversation memory (user-level facts persisted across
    separate conversation_ids) — a genuinely different kind of
    memory from what this module provides
```

The scaling challenge here is the same one flagged for the Gateway's rate limiter: v0.1's state lives in process memory, and every version after this is fundamentally about moving that state somewhere shared, then making it smarter about what to keep once "everything, forever" stops being an option a context window (or a budget) can afford.

## 11. Real-world Examples

- **OpenAI Agents SDK / Assistants API** — the `thread` concept is a direct analog: a persisted conversation identified by an id, with messages appended across multiple API calls, exactly the role `conversation_id` plus `MemoryStore` plays here.
- **LangGraph** — its checkpointer abstraction (`MemorySaver`, `PostgresSaver`, etc.) persists graph/conversation state across invocations behind a pluggable backend interface — the same Protocol-plus-swappable-implementation shape as `MemoryStore`/`InMemoryStore`.
- **LangChain** — `ConversationBufferMemory` (raw history) vs. `ConversationSummaryMemory` (compressed history) is the productionized version of exactly the fork this module defers: replay everything vs. summarize old turns.
- **Langfuse** — traces a conversation across turns primarily for observability rather than for replay, but it shares the same underlying need: a stable identifier a multi-turn interaction can be grouped under.
- **Redis** — the standard real-world backend for exactly this kind of session/conversation state once a single process's memory stops being enough, referenced directly in this module's own Production Evolution.

## 12. Common Mistakes

- **Persisting or replaying the entire `messages` list on every append instead of only what's new.** Duplicates history on every turn and makes a conversation's stored size grow quadratically instead of linearly. This module slices `messages[len(history):]` specifically to avoid it.
- **Returning a live reference to internal storage from `load()` instead of a copy.** A caller mutating the returned list (which `RuntimeEngine` does immediately) would silently corrupt the store's internal state on the next `load()` for the same conversation.
- **Assuming a test double that records a mutable argument is capturing a snapshot.** As this module's own development showed: if the code under test mutates that argument later, a reference-holding fake will report the argument's *final* state for every call, not its state at the time of each call — a subtle, hard-to-spot source of flaky-seeming test failures that only appear once multi-call flows (like a tool loop) are involved.
- **Building a summarization/compression strategy speculatively, before any conversation has actually exceeded context.** Guessing at "the right way to compress a conversation" with zero real usage data risks building the wrong abstraction — the same discipline this platform already applied to staying single-provider and single-tool before generalizing.
- **Treating "in-memory" as good enough for production without saying so out loud.** A store that silently loses all state on restart, or diverges across replicas, needs that limitation documented where engineers will see it — not discovered the first time a Gateway pod restarts mid-conversation.

## 13. Best Practices

- Keep storage behind an interface (`MemoryStore`) from the very first implementation, even a trivial in-memory one — it's what makes swapping backends later a wiring change, not a rewrite.
- Persist only what's new on each write; never re-derive "the whole history" from scratch on every append.
- Return copies, not internal references, from anything that hands back mutable state.
- Only persist state from a request that actually succeeded — a partially-completed, failed operation shouldn't leave a corrupt or confusing partial record behind.
- When a test double records a mutable argument, decide explicitly whether it needs to snapshot (copy) that argument — don't assume reference semantics are safe by default.

## 14. Interview Knowledge

**Must Know**
- Why session/conversation state is kept behind a storage interface rather than hardcoded to a specific backend — the Dependency Inversion pattern applied a fourth time in this platform.
- The distinction between "stateless request" and "stateful conversation" APIs, and what each requires from the server (nothing vs. a persisted, addressable history).
- Why mutable-argument aliasing (passing a list by reference, then mutating it) is a classic source of subtle bugs, especially in code that also records or caches that reference elsewhere (like a test double).

**Good to Know**
- The trade-off between raw history replay (this module's approach) and summarized/compressed memory, and why the former is the correct starting point before the latter is justified by real usage.
- Why persistence should happen only after a unit of work fully succeeds (the "don't persist on the failure path" decision made here for the tool-loop-exceeded case).
- Single-process vs. distributed state, and why an in-memory store is a legitimate v0.1 choice but not a production-scale one the moment there's more than one server instance.

**Advanced**
- Designing a summarization/truncation strategy for long conversations: sliding window, running summary, hybrid (keep recent turns verbatim, summarize the rest).
- Semantic memory (embedding-based retrieval of relevant past context) as a fundamentally different capability from turn-by-turn conversation replay, and when a platform needs both.
- Consistency and routing concerns in a multi-replica deployment when conversation state isn't yet centralized (sticky sessions vs. shared storage as two different fixes for the same problem).

## 15. Key Takeaways

1. Memory is what turns a sequence of stateless requests into a real, continuing conversation — it persists and reloads `ChatMessage` history under a caller-supplied `conversation_id`, nothing more.
2. `MemoryStore` is a Protocol, and `RuntimeEngine` depends on it exactly like it depends on `ModelProvider` and `ToolRegistry` — the same dependency-inversion pattern used consistently across this entire platform.
3. Only what's new since the last load gets persisted (`messages[len(history):]`), and only on the success path — a conversation's stored history never contains duplicated turns or the wreckage of a failed tool loop.
4. `InMemoryStore` is a deliberate, explicitly-limited v0.1 choice — single-process, no expiration, no summarization — with each limitation named as a specific, later, evidence-driven decision rather than solved speculatively now.
5. This module's own development surfaced a real, general bug class: a test double recording a mutable argument by reference instead of by value silently breaks the moment the code under test mutates that argument afterward — worth remembering any time a fake or spy captures something mutable.

## Further Reading

1. OpenAI — [Assistants API: Threads](https://platform.openai.com/docs/assistants/how-it-works) (official docs) — the closest real-world analog to `conversation_id` plus persisted history.
2. LangGraph — [Persistence / checkpointers](https://langchain-ai.github.io/langgraph/concepts/persistence/) — a well-documented pluggable-backend design for exactly this kind of state.
3. LangChain — [Memory types](https://python.langchain.com/docs/modules/memory/types/) — a survey of history-replay vs. summarization strategies, useful background for the v0.2 decision this module deliberately defers.
4. Redis — [Session storage patterns](https://redis.io/docs/manual/patterns/) — reference for the v0.2 shared-backend evolution named in Production Evolution.
5. Martin Fowler — [Mocks Aren't Stubs](https://martinfowler.com/articles/mocksArentStubs.html) — background on test-double semantics, directly relevant to the reference-vs-copy bug this module's development surfaced.

## Next Module

**Evaluation** (`ai_platform/evaluation/`, new) or **Tracing/Observability** (`ai_platform/tracing/`, new) — both are natural next steps now that Runtime produces multi-turn, tool-using, persisted conversations worth measuring. Tracing is the more foundational of the two: before evaluating output *quality*, it's worth being able to see what actually happened on a request (which provider call, which tools, how many tool-loop iterations, from which conversation) — the `ProviderResponse.input_tokens`/`output_tokens` fields the Provider tutorial flagged as "ready for cost accounting whenever Runtime wants it" are still sitting unused, which is exactly the gap a Tracing module would close first.
