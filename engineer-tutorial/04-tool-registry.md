# 04 — The Tool Registry Module

*Internal onboarding doc — AI Platform, Tool Registry component (`ai_platform/tools/`)*

## 1. Executive Summary

The Tool Registry is what turns a chat completion into an agent. Until this module existed, `RuntimeEngine` could only do one thing: send messages to a model and return whatever text came back. There was no way for the model to look anything up, call an API, or take an action in the world — every "capability" had to already be encoded as knowledge in the model's weights.

`ToolRegistry` defines what a tool *is* platform-wide (a name, a description, a JSON-schema of arguments, and an async function to run) and gives Runtime a place to look one up by name. `RuntimeEngine` becomes the registry's first, and so far only, consumer: it hands the registry's tool definitions to the Provider on every call, and when the model asks to use one, executes it and loops back.

This module also did something the previous three didn't: it forced a real change to types that Gateway, Provider, and Runtime all already depended on (`ChatMessage`, `ProviderResponse`). That's a meaningful signal — it's the first proof that the platform's generic types were built to grow, not just to work for the first use case.

## 2. The Problem

A model that can only produce text is a novelty, not a platform. Real usefulness comes from letting the model trigger deterministic, verifiable actions — run a calculation, query a database, call an internal API — and use the result to answer accurately instead of guessing. Anthropic's Messages API (like every major provider) supports this natively: the model can respond with a `tool_use` block instead of (or alongside) text, and the caller is expected to execute that tool and send a `tool_result` back in the next turn.

Without a Tool Registry, "supporting tools" degenerates into one of two bad shapes:
- **Tool definitions and execution logic live inside `RuntimeEngine` directly** — every new tool means editing Runtime's code, and Runtime accumulates an ever-growing pile of `if name == "..."` branches.
- **Tool definitions live wherever a route or script happens to need them** — no single source of truth for "what can this model actually call," which is both an engineering mess and a security problem (nothing enforces which tools are exposed to which caller).

## 3. Motivation

This is the same additive-registration pattern already used twice in this platform: `AnthropicProvider` proved that adding a second `ModelProvider` should be a new class, not an edit to Runtime; `ToolRegistry` applies the identical idea one level over — adding a third tool should be a new class plus one `register()` call, not a new branch in `RuntimeEngine.handle_chat`. Once tools are registered rather than hardcoded, Runtime's job becomes purely mechanical: "hand the model whatever's registered, execute whatever it asks for by looking it up" — it never needs to know that `calculator` exists, only that *something* might be registered under that name.

## 4. Responsibilities

**Tool Registry should:**
- Define the `Tool` protocol (`interfaces.py`): a `definition` property (`ToolDefinition` — name, description, JSON schema) and an async `execute(**kwargs) -> str`
- Hold every registered tool, keyed by name (`registry.py::ToolRegistry`)
- Produce the list of `ToolDefinition`s to hand a `ModelProvider` on each call (`definitions()`)
- Look a tool up by name for execution (`get()`), raising a platform exception (`ToolNotFoundError`) if the model asks for something unregistered

**Tool Registry should NOT:**
- Decide *when* to call a tool, or how many times to loop — that's Runtime's job (`RuntimeEngine`'s tool-calling loop)
- Know which provider is asking, or how to translate `ToolDefinition` into that provider's wire format — that's the Provider layer's job (`AnthropicProvider._tool_to_anthropic`)
- Enforce per-caller authorization ("can this API key use the `calculator` tool") — a future Auth/RBAC concern, same boundary the Gateway tutorial already flagged for model access
- Retry a failing tool call or sandbox its execution — real production concerns, deliberately out of scope for a v0.1 registry with one deterministic example tool

## 5. Architecture

```
                 RuntimeEngine
                    │  registry.definitions()  →  passed to ModelProvider.complete(tools=...)
                    │
                    │  registry.get(name)  →  Tool, to execute a requested call
                    ▼
      ┌─────────────────────────────┐
      │   ToolRegistry                 │   ai_platform/tools/registry.py
      │   { name: Tool }                │
      └──────────────┬────────────────┘
                      │ holds instances implementing
                      ▼
      ┌─────────────────────────────┐
      │   Tool (Protocol)              │   ai_platform/tools/interfaces.py
      └──────────────┬────────────────┘
                      │ implemented by
                      ▼
      ┌─────────────────────────────┐
      │   CalculatorTool               │   ai_platform/tools/builtin.py
      │   (example implementation)      │
      └─────────────────────────────┘
```

Upstream: `RuntimeEngine` holds a `ToolRegistry` reference (constructor-injected, same DI pattern as its `ModelProvider`), and uses it two ways per completion cycle — once to fetch `definitions()` before calling the model, once to `get()` a tool after the model asks for one. Downstream: nothing — `Tool` implementations (like `CalculatorTool`) are leaves in the dependency graph; they don't call back into Runtime or Provider.

Sideways: the Provider layer had to grow a translation function (`AnthropicProvider._tool_to_anthropic`) to turn a `ToolDefinition` into Claude's tool schema, and a parsing path to turn a `tool_use` content block back into a `ToolCall`. The registry itself never touches Anthropic's shape — that translation stays quarantined in Provider, exactly where module 02 said vendor-specific translation belongs.

## 6. Request Flow

Walking through a request where the model needs the calculator (continuing from where the Runtime tutorial left off):

1. **`RuntimeEngine.handle_chat`** fetches `self._tools.definitions()` — today, just `calculator`'s `ToolDefinition` — once, before the loop starts.
2. **First `provider.complete(messages, model=..., tools=[calculator_definition])`** — `AnthropicProvider` translates each `ToolDefinition` into `{"name", "description", "input_schema"}` and includes it as `tools=[...]` in the Claude API call.
3. **Claude decides it needs the tool** and responds with a `tool_use` content block (`id`, `name="calculator"`, `input={"operation": "add", "a": 2, "b": 3}`) instead of (or alongside) text.
4. **`AnthropicProvider`** parses that block into a `ToolCall` on `ProviderResponse.tool_calls`, and preserves the *entire* content block list (text + tool_use) on `ProviderResponse.message.content` — not collapsed to a string, because the exact `tool_use` block has to be echoed back as conversation history in step 6.
5. **`RuntimeEngine`** sees `result.tool_calls` is non-empty, so it does *not* return yet. It appends the assistant's message (the tool_use blocks) to the running `messages` list, then calls `self._run_tool_calls(result.tool_calls)`: for each call, `self._tools.get("calculator")` returns the registered `CalculatorTool`, and `await tool.execute(operation="add", a=2, b=3)` returns `"5"`.
6. **A new `ChatMessage(role="user", content=[ToolResultBlock(tool_use_id=..., content="5")])`** is appended to `messages` — the exact reply shape Claude's API requires, addressed back to the tool_use block by id.
7. **Second `provider.complete(messages, ...)`** — now the conversation history includes the tool call and its result. Claude responds with plain text, e.g. `"The answer is 5."`, and no `tool_use` block this time.
8. **`RuntimeEngine`** sees `result.tool_calls` is empty, and returns `ChatResponse(message=result.message, model=request.model)` — loop ends, exactly like the non-tool case Runtime already handled.
9. **If the model never stops requesting tools**, the loop runs `_MAX_TOOL_ITERATIONS` (5) times and then raises `RuntimeToolLoopExceededError`, which the Gateway's existing error handler maps to `503` — no new Gateway code needed, same as every `ProviderError` before it.

## 7. Design Decisions

**Why does `ChatMessage.content` become `str | list[ContentBlock]` instead of adding a separate `ToolMessage` type?**
Because a single conversation turn can genuinely contain a mix of things — text plus a tool call, or just a tool result — and Anthropic's actual wire format models it that way (a `content` array of typed blocks), not as separate message classes. Keeping `content: str` as one arm of the union means every existing plain-text message (system prompts, user turns, non-tool assistant replies) stays exactly as simple as before this module existed — the change is additive, not a rewrite of every call site.

**Why does `ProviderResponse.message.content` stay a plain string for text-only replies, but become a block list the moment a tool is involved?**
Because that's the actual constraint, not an aesthetic choice: Claude's API requires the assistant's original `tool_use` block to be echoed back verbatim in conversation history when its `tool_result` is sent — collapsing it to a text summary like `"called calculator"` would produce an API error on the next turn. Text-only replies have no such constraint, so keeping them as plain strings preserves backward compatibility with every test and caller written before tools existed.

**Why is `ToolDefinition` defined in `common/schemas.py` rather than in `tools/`?**
Both `providers/anthropic_provider.py` (translates it into Claude's schema) and `tools/registry.py` (produces it from a registered `Tool`) need the same shape, and neither should import the other's module wholesale just for one type. `common/` is already the platform's home for cross-cutting types every layer shares (`ChatMessage` is the precedent) — `ToolDefinition` fits the same role.

**Why does `RuntimeEngine` catch nothing around `tool.execute(...)` — no try/except for a misbehaving tool?**
Consistent with the "ship the thinnest correct composition first" principle from the Runtime tutorial: feeding tool-execution errors back to the model as a recoverable `tool_result` (so it can retry with different arguments) is a real, valuable production pattern — but it's a deliberate design decision with its own shape questions (what does an "error" tool_result look like? does the loop count it against the iteration cap?), not something to bolt on silently. For now, a tool exception propagates like any other unexpected error, and is a named improvement in Production Evolution below.

**Why a hardcoded `_MAX_TOOL_ITERATIONS = 5` instead of a configurable setting?**
Same reasoning the Provider layer used for staying single-vendor: there's exactly one real tool and no evidence yet for what the right limit is under real usage. A magic number that's easy to find and change beats a `Settings` field added speculatively before anyone knows if 5, 10, or 3 is actually the right ceiling.

## 8. Alternative Designs

| Alternative | Why not |
|---|---|
| **Hardcode tool definitions and dispatch directly inside `RuntimeEngine`** | Every new tool becomes a Runtime code change and a growing `if/elif` chain — the exact anti-pattern the Provider layer's "no branching on vendor string" decision already rejected once in this platform. |
| **A separate `ToolMessage` class instead of extending `ChatMessage.content`** | Would fork the message type Gateway, Provider, and Runtime all already depend on into two incompatible shapes, and every function taking `list[ChatMessage]` would need to handle both. A union on `content` keeps one message type, one signature, everywhere. |
| **Sandbox tool execution in a subprocess/container from day one** | A legitimate and eventually necessary production concern once tools can do things like execute arbitrary code or hit untrusted URLs — rejected for now because the only tool that exists is a pure, side-effect-free calculator; building a sandbox against zero real risk is designing ahead of evidence. |
| **Let each `Tool` decide when to stop being called (self-terminating tools)** | Pushes a Runtime-level orchestration decision (when has the loop gone on too long) down into tool implementations, which shouldn't need to know they're part of a loop at all. The iteration cap belongs in `RuntimeEngine`, the one place that actually orchestrates the loop. |

## 9. Trade-offs

**Gained:** Runtime can now compose arbitrary tools without being edited — adding a second tool is a new class plus one `registry.register(...)` call in `api/dependencies.py`. The tool-calling loop, translation logic, and registry are each independently unit-tested (`tests/tools/`, expanded `tests/runtime/test_engine.py`, expanded `tests/providers/test_anthropic_provider.py`) without needing a real Claude call or a real external tool.

**Cost:** `ChatMessage` is now a slightly more complex type than it needs to be for the common case (a plain string always worked before; now every consumer that reads `.content` has to consider it might be a block list). Real risk surfaces are also now open that didn't exist in v0.1 — a tool that's slow, that fails, or that a model calls forever — and this pass deliberately defers all three (timeouts, error recovery, and configurable iteration limits) rather than solving them speculatively.

## 10. Production Evolution

```
v0.1 (this module)
  one deterministic example tool (CalculatorTool)
  no error recovery — a failing tool raises, ending the request
  hardcoded max-iteration cap (5)
  no per-caller tool authorization
        │
        ▼
v0.2
  tool execution errors caught and fed back as a tool_result
    the model can see and react to ("division by zero: adjust your input")
  per-tool timeout, so one slow tool can't hang the whole request
  real tools with actual side effects (HTTP calls, DB reads)
        │
        ▼
Enterprise version
  per-caller / per-tenant tool allowlists (RBAC over which tools
    a given API key's requests may expose to the model)
  audit logging of every tool call: who, which tool, what arguments,
    what it returned — a compliance and debugging requirement
  parallel tool-call execution when the model requests multiple
    tools in one turn (today executed sequentially)
  sandboxed/isolated execution for tools that run arbitrary code
        │
        ▼
Large-scale platform
  a tool marketplace / dynamic tool loading (tools registered from
    external services, not just in-process Python classes)
  cost/latency accounting per tool, feeding the same audit-logging
    module the Provider tutorial already anticipated for token usage
  circuit breaker around a tool that's failing repeatedly, so Runtime
    stops offering it to the model until it recovers
```

The scaling challenge here is trust boundary, not state or scope: v0.1's one tool is fully trusted, in-process, and side-effect-free. Every step after that is about safely expanding what the model is allowed to trigger — timeouts and error handling first, then authorization and auditing, then isolation — because the whole point of tools is letting the model cause real effects, which is exactly what makes this the platform's first genuine security surface.

## 11. Real-world Examples

- **Anthropic's tool use / function calling** — the exact mechanism this module implements against: `tool_use` content blocks in the response, `tool_result` blocks addressed back by id in the next turn.
- **OpenAI Agents SDK** — its `FunctionTool` and the agent's tool-calling loop play the same role as `Tool`/`ToolRegistry`/`RuntimeEngine`'s loop here, at a more fully-featured level (parallel calls, structured output tools, hosted tools).
- **LangChain / LangGraph** — `BaseTool` and `ToolNode` are the direct analogs of this module's `Tool` protocol and the tool-execution step in a graph, respectively.
- **Dify** — its tool plugin system lets tools be registered and exposed to an agent workflow without editing the workflow engine itself, the same additive-registration principle as this registry.
- **Model Context Protocol (MCP)** — a standardized way to expose tools (and resources) to a model across process/network boundaries; a natural evolution path once this platform's tools need to live outside the same Python process as Runtime.

## 12. Common Mistakes

- **Branching on tool name inside Runtime instead of going through a registry.** Reintroduces the exact coupling this module exists to avoid — Runtime should only ever call `registry.get(name)`, never know that `"calculator"` specifically exists.
- **Collapsing a tool-call response to plain text before sending it back to the model.** Breaks the API contract — Claude expects the exact `tool_use` block echoed back, not a paraphrase of it.
- **Forgetting the iteration cap.** A model that keeps requesting tools (due to a confusing tool description, bad arguments, or a genuinely unsolvable request) will loop forever without one — this is not a hypothetical failure mode once real tools exist.
- **Treating a tool's JSON schema as an afterthought.** A vague or incomplete `input_schema` is one of the most common causes of a model calling a tool with wrong or missing arguments — the schema is effectively the tool's API contract with the model, and deserves the same care as any other API contract.
- **Testing the tool-calling loop only against a real model.** Non-deterministic and slow; `FakeModelProvider` returning a scripted sequence of responses (tool call, then final answer) is what makes `RuntimeEngine`'s loop logic actually unit-testable.

## 13. Best Practices

- Keep tool definitions and execution behind a registry, never hardcoded into the orchestration layer that calls them.
- Preserve exact provider-required structure (like `tool_use` blocks) through the round trip — don't simplify data you'll need to send back unchanged.
- Always cap agentic loops with a hard iteration limit; "the model will eventually stop" is not a safety mechanism.
- Treat a tool's schema as a contract worth writing carefully — it's the interface the model programs against, same as any documented API.
- Test orchestration logic (the loop) against fakes that can script multi-step scenarios, independent of testing the translation logic (Provider) or the tool's own behavior.

## 14. Knowledge

**Must Know**
- What "function calling" / "tool use" means for an LLM API, and the two-step round trip it requires (model requests a call → caller executes and returns a result).
- Why a registry/plugin pattern (register by name, look up by name) is preferable to hardcoded dispatch for an extensible set of capabilities.
- Why an agentic loop needs a hard iteration cap.

**Good to Know**
- How `tool_use`/`tool_result` block linkage works via an id, and why the exact response structure must be preserved across a multi-turn tool exchange.
- The difference between a union type on a single message class (this platform's approach) vs. separate message subtypes per content kind, and the trade-offs of each.
- Why tool execution is a genuine security boundary — the model is choosing what code runs, based on untrusted (or at least model-generated) input.

**Advanced**
- Parallel vs. sequential tool-call execution when a model requests multiple tools in one turn, and the complexity of merging results back into one conversation turn.
- Sandboxing strategies for tools that execute arbitrary or less-trusted logic (subprocess isolation, containers, WASM).
- The Model Context Protocol (MCP) as a standardized, cross-process alternative to an in-process tool registry — what it solves that this module's simpler design does not (yet) need to.
- Designing tool-authorization (RBAC over which tools a given caller's requests may expose) as a policy layer above the registry, not inside it.

## 15. Key Takeaways

1. The Tool Registry is what turns a chat completion into an agent — it defines what a tool is platform-wide and lets Runtime execute one by name, without Runtime ever hardcoding which tools exist.
2. Adding a tool is additive registration (`registry.register(tool)`), the same pattern that made adding a `ModelProvider` additive — the platform now has two independent axes (providers, tools) that grow without touching Runtime's orchestration code.
3. `ChatMessage.content` had to grow into a union (`str | list[ContentBlock]`) because tool-call turns must preserve exact provider-required structure (the `tool_use` block) to round-trip correctly — proof the platform's core types were built to extend, not just to fit the first use case.
4. `RuntimeEngine`'s tool-calling loop is capped at a hard iteration limit — an agentic loop without one is a real, not hypothetical, failure mode the moment tools exist.
5. This module opens the platform's first genuine security surface (the model choosing what code executes) — v0.1 deliberately ships with one trusted, side-effect-free tool and defers authorization, sandboxing, and error recovery to versions that have real requirements to design against.

## Further Reading

1. Anthropic — [Tool use (function calling) guide](https://docs.anthropic.com/en/docs/build-with-claude/tool-use) (official docs) — the exact API contract this module's Provider-layer translation implements against.
2. Model Context Protocol — [Introduction](https://modelcontextprotocol.io/introduction) — the standardized, cross-process evolution of what this module's in-process registry does today.
3. OpenAI — [Function calling guide](https://platform.openai.com/docs/guides/function-calling) — useful comparison point for how a second major vendor models the same tool-use round trip.
4. LangGraph — [Tool calling / ToolNode concepts](https://langchain-ai.github.io/langgraph/concepts/low_level/#tools) — a graph-based take on the same execute-and-loop-back pattern.
5. OWASP — [Top 10 for LLM Applications: Excessive Agency](https://owasp.org/www-project-top-10-for-large-language-model-applications/) — background on why tool execution is treated as a security boundary, relevant once this registry grows past one trusted tool.

## Next Module

**Memory** (`ai_platform/memory/`, new). Runtime can now call tools within a single request, but every `handle_chat` call still starts from scratch — there's no way for a conversation to span multiple HTTP requests, and no way to compress a long tool-calling exchange (like the one this module introduced) once it grows past what fits in context. Memory is where conversation history gets persisted and retrieved across requests, and where a real strategy for summarizing or truncating long tool-loop exchanges will need to be designed — deliberately, with the same "don't build it until there's a real shape to build against" discipline used for every module so far.
