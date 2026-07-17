# 11 — The Planning Module

*Internal onboarding doc — AI Platform, Planning component (`ai_platform/planning/`)*

## 1. Executive Summary

Every module through Sandbox made `RuntimeEngine` more capable, more observable, or more isolated — none of them changed *how* it decides what to do next. `RuntimeEngine.handle_chat` is, and remains after this module, a purely reactive loop: call the model, see if it asked for a tool, execute it, call the model again, repeat up to `_MAX_TOOL_ITERATIONS`. Nothing outside the model's own next-token choice ever holds an explicit, inspectable idea of "here's the sequence of steps this request actually needs."

Planning closes part of that gap, deliberately narrowly. It defines a `Planner` protocol and a `Plan` — an ordered list of `PlanStep`s, each a natural-language description plus an optional `tool_hint` — and ships one real implementation, `LLMPlanner`, which asks the model itself, in a dedicated completion separate from the execution loop's own calls, to decompose a request before that loop runs. Critically, v0.1 is **observational only**: the resulting `Plan` is recorded as a `"planner.plan"` `Span` (visible through the trace viewer added in the Tracing module's most recent extension) and then discarded — the reactive tool loop executes exactly as it would with no `Planner` configured at all. This module answers "what would a good plan for this request look like," not yet "make Runtime actually follow one."

## 2. The Problem

For a single-step request ("what's 47 times 89?"), the reactive loop is the right shape — there's nothing to plan. For a request with real internal structure ("plan a birthday party for 10 people"), nothing today produces or exposes an upfront decomposition:

- **There's no inspectable plan, only a transcript.** If the model's response to a multi-part request is good, the "plan" it followed only ever existed implicitly inside one completion — there's no structured object a caller, a UI, or an evaluation harness could read to answer "what steps did this request actually break down into."
- **Debugging a bad multi-step answer means re-reading prose, not inspecting a data structure.** Tracing (module 06) solved this for *execution* — every provider/tool call is a `Span` — but there was nothing analogous for the *decomposition* a request implies, since the reactive loop never produces one as a distinct artifact.
- **Every future consumer that would want a plan (a UI showing "here's what I'm about to do," an approval gate before executing sensitive steps, Evaluation scoring plan quality, not just final-answer quality) has nothing to build on**, the same "no consumer to build against yet" gap Tracing's tutorial named for cost accounting before it shipped.

## 3. Motivation

Planning is one of the most consistently named components in agent architectures — ReAct interleaves reasoning and acting one step at a time (what `RuntimeEngine`'s loop already does), while plan-and-execute architectures (LangChain's `PlanAndExecute`, BabyAGI, AutoGPT's task queue) produce an explicit plan first and then work through it. This module deliberately does not adopt plan-and-execute's *execution* model yet — only its *artifact*. That's a considered scope cut, not an oversight: this platform has repeatedly shipped visibility before enforcement (Sandbox shipped isolation without a per-tool policy; Tracing shipped recording without export) and there's no evidence yet that this platform's reactive loop is insufficient for the tasks it's actually being asked to do. Producing a `Plan` and recording it costs one extra completion and changes nothing else; making `RuntimeEngine` *follow* a plan would mean rewriting its core loop on a hypothesis, not a demonstrated need.

## 4. Responsibilities

**Planning should:**
- Define what an ordered decomposition looks like (`Plan`, `PlanStep` — `types.py`): a list of steps, each a `description` and an optional `tool_hint`
- Define the `Planner` protocol (`interfaces.py`): a single `plan(request, tools) -> Plan` method
- Ship a real implementation (`LLMPlanner` — `llm_planner.py`) that asks the model, via the existing `ModelProvider` interface, to produce that decomposition as JSON — grounded in the tools actually registered, so a `tool_hint` names something real rather than something the model invented
- Tolerate the model not following instructions exactly — a markdown-fenced or malformed response degrades to an empty `Plan`, never a crash

**Planning should NOT:**
- Decide *when* to plan or *what happens* with the result — that's `RuntimeEngine`'s call, exactly like Tracing's `Span` timing is the caller's responsibility, not the sink's
- Change execution based on the plan it produces — v0.1 is observational only; see Design Decisions
- Let a `tool_hint` actually invoke a tool — the planning completion never passes `tools=` through to `ModelProvider.complete()`, specifically so the model can only *describe* a tool, never *call* one, during planning
- Retry, refine, or re-plan — one completion, one `Plan`, per request; iterative/self-correcting planning is a named Production Evolution item, not built now

## 5. Architecture

```
                 RuntimeEngine.handle_chat
                    │  before the reactive tool loop starts:
                    │  if planner present:
                    │    plan = await planner.plan(request, tool_definitions)
                    │    record as Span("planner.plan", {step_count, steps})
                    │    (errors here are swallowed, not re-raised)
                    ▼
      ┌─────────────────────────────┐
      │   Planner (Protocol)            │   ai_platform/planning/interfaces.py
      │   plan(request, tools) -> Plan  │
      └──────────────┬────────────────┘
                      │ implemented by
                      ▼
      ┌─────────────────────────────┐
      │   LLMPlanner                    │   ai_platform/planning/llm_planner.py
      │   asks ModelProvider for a plan │
      │   (dedicated completion, no     │
      │    tools= passed through)       │
      └─────────────────────────────┘

      ┌─────────────────────────────┐
      │   Plan / PlanStep                │   ai_platform/planning/types.py
      │   steps: list[PlanStep]          │
      │   PlanStep: description,          │
      │             tool_hint | None      │
      └─────────────────────────────┘
```

Upstream: `RuntimeEngine` holds an optional `Planner` reference (constructor-injected, same DI pattern as `Tracer`/`Sandbox`/`MemoryStore`), and is the only place a `Planner` is ever invoked. Downstream: `LLMPlanner` calls `ModelProvider.complete()` — the same interface `RuntimeEngine`'s own loop depends on — so a planning call is indistinguishable, from the provider's point of view, from any other completion; it's just one more request with a distinct system prompt. Sideways: the resulting `Plan` is recorded through the same `Tracer`/`InMemoryTracer` machinery Tracing already built, as a `"planner.plan"` `Span` — Planning added no new storage or retrieval path of its own, and a `Plan` is visible through `GET /v1/traces/{trace_id}` for free.

## 6. Request Flow

Continuing from the trace-viewer example (Tracing's most recent extension), now with a `Planner` configured — which it is by default in the Gateway's real composition root (`api/dependencies.py`):

1. **`RuntimeEngine.handle_chat`** computes `trace_id` and loads history exactly as before, then — before the reactive tool loop starts — calls its new private `_plan(trace_id, request, tool_definitions)` helper.
2. **`_plan` times the call** with `time.monotonic()` (same pattern as `_complete`/`_run_tool_calls`) and calls `self._planner.plan(request, tool_definitions)`.
3. **`LLMPlanner.plan`** builds a system message combining the planning instructions with a description of every registered tool (name + description, so `tool_hint` values can reference something real), plus the request's own messages, and calls `ModelProvider.complete(...)` — deliberately *without* passing `tools=tool_definitions` through, so the model can only describe tool usage in its plan text, never actually trigger a `tool_use` block during planning.
4. **The model's response is expected to be a JSON array** of `{"description": ..., "tool_hint": ...}` objects. In practice (confirmed against the real Anthropic API during development), the model reliably wraps this in a markdown code fence (` ```json ... ``` `) despite being told to respond with *only* the JSON — `_strip_code_fence` strips one off before `json.loads`, so this common case still parses instead of silently degrading.
5. **A response that still isn't valid JSON, isn't a JSON array, or whose items are missing `description`** yields an empty `Plan()` rather than raising — parsing failure is data, not an exception, at this layer.
6. **Back in `RuntimeEngine._plan`**, the resulting `Plan` is recorded as `Span("planner.plan", {"step_count": N, "steps": [{"description", "tool_hint"}, ...]})`. **If `planner.plan(...)` itself raised** (a provider timeout, an auth error, anything), `_plan` catches it, records the same span shape with `error` set instead of `attributes`, and **returns without re-raising** — the one place this module's error handling deliberately differs from every other collaborator's call site in `RuntimeEngine`.
7. **The reactive tool loop then runs exactly as it always has** — `_plan`'s output is never consulted again. Whatever the model actually does, iteration by iteration, is unaffected by whatever plan was recorded.
8. **After the request**, `GET /v1/traces/{trace_id}` shows the `"planner.plan"` span alongside every `"provider.complete"`/`"tool.execute"` span from the same request — a live example (from real testing) for "what's 47 times 89, and what percent of 10000 is that?": a 4-step plan recorded first, then the actual 3-completion, 2-tool-call reactive execution that followed it, visibly unconstrained by the plan's own shape.

## 7. Design Decisions

**Why is v0.1 observational-only instead of plan-driven execution?**
This was an explicit choice, not a default: the alternative (`RuntimeEngine` actually follows the plan step-by-step) is a materially larger, riskier change — replacing the core loop this platform has run since module 03 — with no evidence yet that reactive tool-calling is insufficient for what this platform is actually asked to do. Observational-only costs one extra completion and changes nothing else about existing behavior; it's the same "ship visibility, defer enforcement" shape Sandbox and Tracing both used.

**Why does `_plan` swallow exceptions instead of re-raising, unlike `_complete` and `_run_tool_calls`?**
`_complete` and `_run_tool_calls` guard calls that are *load-bearing* — if the model or a tool fails, the request genuinely cannot be answered, so the span records the error and the exception still propagates. A `Planner` failure is different in kind: the plan is never consulted by anything that produces the actual response, so a planning outage must not turn into a chat outage. Recording an error span (so the failure is still visible in the trace) while returning normally is the correct behavior for a component whose entire contract is "observe, never affect."

**Why does `LLMPlanner` describe tools in the prompt instead of passing `tools=` to `ModelProvider.complete()`?**
Passing real `ToolDefinition`s through would let the model return an actual `tool_use` block instead of the JSON plan text `_parse_plan` expects — the same API surface `RuntimeEngine`'s real loop uses to let the model *invoke* a tool would let it invoke one during planning too, which is not what a planning call is for. Describing tools in the prompt text (name + description) gives the model enough grounding to produce plausible, real `tool_hint` values without ever risking a live tool call as a side effect of asking for a plan.

**Why does `get_planner(provider)` in `dependencies.py` take a parameter, unlike its zero-arg `@lru_cache` siblings (`get_tracer`, `get_sandbox`)?**
`LLMPlanner` needs a `ModelProvider`, and the platform already constructs exactly one (`create_anthropic_provider(...)`) inside `get_runtime_client()` for the reactive loop itself — `get_planner` reuses that same instance rather than being handed the means to build an independent one. `get_runtime_client()` is itself `@lru_cache`d, so `get_planner` doesn't need its own cache to avoid redundant construction; it exists as its own function purely so "the one place the concrete Planner backend is chosen" has a name, matching the file's established convention even though its signature can't quite match the pattern byte-for-byte.

**Why is `attributes: dict` on the recorded span shaped as `{"step_count", "steps": [{"description", "tool_hint"}, ...]}` instead of just step counts?**
Tracing's own design decision (module 06) already settled `Span.attributes` as an untyped `dict` because there's no consumer yet that needs more than "read it back for display or debugging." A `"planner.plan"` span that only recorded a count would defeat the entire purpose of making planning inspectable — the descriptions and tool hints are exactly the content a developer debugging "why did the model do these five things" would want to read.

## 8. Alternative Designs

| Alternative | Why not |
|---|---|
| **Plan-driven execution from v0.1** (RuntimeEngine actually follows the plan) | A much larger, riskier rewrite of the core loop with no evidence of need yet — the same "don't build ahead of evidence" reasoning this platform used to defer Sandbox until module 04's tool registry had a real trust boundary to protect. |
| **A rule-based/deterministic planner** (pattern-match the request into known step templates) | Can't generalize past whatever cases it anticipates — the same reasoning that kept this platform on a single real `ModelProvider` and a single real `ToolRegistry` tool: ship the version that actually generalizes to arbitrary requests, not a narrow stand-in. |
| **Pass `tools=tool_definitions` to the planning completion, same as the real loop does** | Would let the model return a `tool_use` block instead of plan text, defeating the parser and risking an unintended live tool invocation during what's supposed to be a pure description step. |
| **Raise on a planner failure, matching `_complete`'s behavior** | Would make an observational, non-load-bearing feature capable of taking down real chat requests — the opposite of what "observational only" is supposed to guarantee. |
| **A structured-output / function-calling based plan format instead of prompted JSON** | A real v0.2 improvement (more reliable than prompting for JSON and hoping) — deferred because it couples this module to provider-specific structured-output APIs before there's a second `Planner` implementation to prove the abstraction holds, the same reasoning that's kept `ModelProvider` itself single-vendor. |

## 9. Trade-offs

**Gained:** every request now produces an inspectable, structured decomposition — visible through the same trace viewer Tracing built — independent of whether the actual answer turned out to need multiple steps. Debugging "why did the model do these things in this order" now has a plan to compare the actual execution against, even though nothing enforces the two matching yet.

**Cost:** because the Planner is wired into the Gateway's real composition root, **every real `/v1/chat` call now makes two completions instead of one** — real added latency (observed: 2–5 seconds for the planning call alone against the real Anthropic API) and real added token cost, for output that is currently recorded but not used to inform the actual answer. This is a genuine, named cost of choosing to wire it in now rather than leave it available-but-unwired; the "leave it opt-in" alternative was explicitly considered and rejected only after weighing that against wanting the feature immediately exercisable end-to-end like every other module in this platform.

## 10. Production Evolution

```
v0.1 (this module)
  one Plan per request, produced by a single prompted completion
  observational only — recorded as a Span, never consulted by execution
  tool_hint grounded via prompt description, not live tool access
  wired into every real Gateway request (real latency/cost per call)
        │
        ▼
v0.2
  plan-driven execution: RuntimeEngine actually follows Plan steps,
    falling back to reactive behavior for steps a Plan didn't anticipate
  structured-output/function-calling based plan generation, replacing
    prompted-JSON-plus-fence-stripping with a provider-native contract
  re-planning: revise the Plan mid-execution when a step's actual
    result diverges from what the plan assumed
        │
        ▼
Enterprise version
  human-in-the-loop approval gates on generated Plans before
    execution, for requests whose steps touch sensitive tools
  plan-quality scoring folded into Evaluation (module 07) — not just
    "was the final answer right" but "was the decomposition sound"
  per-tenant/per-tool planning policy (which requests get planned at
    all, trading the per-call cost against the visibility gained)
        │
        ▼
Large-scale platform
  multi-agent workflows: a Plan's steps distributed across multiple
    specialized agents/services rather than one RuntimeEngine loop —
    the natural point where "Planning" becomes "Workflow orchestration"
  persisted, resumable plans spanning multiple requests/sessions,
    not regenerated fresh on every handle_chat call
```

The scaling challenge here is trust, not data model — `Plan`/`PlanStep` barely need to change from v0.1 to Enterprise; what changes is how much the platform is willing to let a generated plan actually *do* without a human or a policy in the loop first.

## 11. Real-world Examples

- **ReAct (Reasoning + Acting)** — the paper behind the interleaved reason-then-act shape `RuntimeEngine`'s reactive loop already implements; this module adds an explicit planning step *in front of* that loop rather than replacing it.
- **LangChain `PlanAndExecute`** — the direct architectural analog: a planner produces a step list, an executor works through it. This module ships the planner half only, deliberately not yet the executor half.
- **BabyAGI / AutoGPT** — early popular agent frameworks built around an explicit task queue a planning step populates and an execution loop consumes — the same two-phase shape, at much larger and less constrained scope than this module's single-completion planner.
- **LangGraph** — represents multi-step agent behavior as an explicit graph of nodes and edges; the natural analog for this module's own "v0.2: plan-driven execution" and "Large-scale: Workflow orchestration" evolution steps.

## 12. Common Mistakes

- **Assuming "respond with ONLY JSON" is sufficient instruction.** Real testing against the live model showed it reliably wraps JSON in a markdown code fence anyway — an LLM-facing parser needs to tolerate the model's actual behavior, not just its instructed behavior. `_strip_code_fence` exists because of this, discovered empirically rather than assumed.
- **Passing real tool definitions into a "just describe what you'd do" call.** Doing so risks the model actually invoking a tool instead of describing one — a planning step and an execution step must stay on genuinely different API surfaces even when they share the same `ModelProvider`.
- **Treating a Planner failure like a Provider failure.** They look similar (both are calls to the same kind of dependency) but have different blast radii — a Provider failure means the request can't be answered; a Planner failure, in an observational-only design, means only that one debugging artifact is missing.
- **Conflating "a Plan was produced" with "the Plan was followed."** Nothing in this module's v0.1 makes that true — the trace shows both the plan and the actual execution, and they are not guaranteed (or currently even checked) to match.

## 13. Best Practices

- Ship a planning/decomposition artifact as pure visibility before wiring it to control execution — the same discipline this platform has applied to every trust- or behavior-affecting addition so far.
- Keep a "describe" call and an "act" call on genuinely separate API surfaces (no `tools=` on a planning completion) even when both go through the same underlying interface.
- Parse LLM-generated structured output defensively — assume markdown fences, prose preambles, and other instruction-following gaps, and degrade to an empty/safe result rather than raising.
- Record a failure in an optional, non-load-bearing collaborator without letting it propagate — the request the collaborator was only ever describing must still succeed.
- Name the real cost of wiring an extra model call into a hot path explicitly (latency, tokens) rather than leaving it implicit — "recommended for now" is a decision that should be revisited with evidence, not left as an unexamined default.

## 14. Knowledge

**Must Know**
- The difference between a *reactive* agent loop (ReAct: decide one step at a time from current state) and a *plan-and-execute* one (decide the whole sequence upfront, then work through it) — and that this module builds the planning half without yet adopting the execution half.
- Why an LLM-facing parser must tolerate the model not following formatting instructions exactly, and why that's normal, not a bug in the prompt.
- Why "observational only" is a real, load-bearing architectural constraint — it's what justifies swallowing this component's own failures instead of propagating them.

**Good to Know**
- Why keeping a "describe" call and an "act" call on separate API surfaces (no `tools=` during planning) prevents an unintended side effect from a call that's supposed to be side-effect-free.
- The cost trade-off of wiring an extra model call into every real request versus leaving a feature available-but-unwired until there's demand for it always-on.
- How `Span.attributes`' untyped `dict` shape (a decision from the Tracing module) let this module attach rich, nested plan data without any schema change to `Tracing` itself.

**Advanced**
- Structured-output / function-calling APIs as a more reliable alternative to prompting for JSON and defensively parsing the result — what changes in `LLMPlanner`'s design if it adopts one.
- Designing plan-driven execution (v0.2) so that a step the reactive loop can't map onto an actual tool call degrades gracefully back to reactive behavior, rather than either blindly forcing the plan or discarding it entirely.
- The point at which "Planning" as a single-request concept becomes "Workflow orchestration" as a multi-step, possibly multi-agent, possibly multi-request concept — and what persistence and resumability that transition requires.

## 15. Key Takeaways

1. `RuntimeEngine`'s reactive tool-calling loop is unchanged by this module — Planning adds a `Plan` as an inspectable artifact, recorded on the trace, without yet making execution follow it.
2. `Planner` is a Protocol with one method (`plan(request, tools) -> Plan`); `LLMPlanner` is its only implementation, asking the model itself via the same `ModelProvider` interface `RuntimeEngine`'s own loop depends on, in a dedicated completion.
3. Tool grounding happens through the prompt (naming registered tools and their descriptions), never through `tools=` on the planning completion itself — the model can describe a tool call in a plan but can never trigger one while planning.
4. A `Planner` failure is recorded (as an error `Span`) and then swallowed, not re-raised — the one call site in `RuntimeEngine` that deliberately breaks from the "record then re-raise" pattern `_complete`/`_run_tool_calls` established, because this component's entire contract is to observe without affecting the request it's describing.
5. Wiring `LLMPlanner` into the Gateway's real composition root means every live `/v1/chat` call pays for two completions instead of one — a real, named cost accepted in exchange for the feature being immediately exercisable end-to-end, not a free addition.

## Further Reading

1. Yao et al. — [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629) — the paper behind the interleaved reason-then-act loop `RuntimeEngine` already implements, and the baseline this module's planning step is added in front of.
2. LangChain — [Plan-and-Execute Agents](https://blog.langchain.dev/planning-agents/) — the direct architectural analog for the planner/executor split this module deliberately only half-adopts.
3. LangGraph — [Concepts: Graphs](https://langchain-ai.github.io/langgraph/concepts/low_level/) — the natural shape for this module's own "v0.2 plan-driven execution" and "Large-scale Workflow orchestration" evolution steps.
4. Anthropic — [Building effective agents](https://www.anthropic.com/research/building-effective-agents) — background on when explicit planning genuinely helps versus when a simpler reactive loop is sufficient, directly relevant to this module's choice to ship planning as observation-only until there's evidence for more.
5. OpenAI / Anthropic structured output and tool-use documentation — background for this tutorial's Production Evolution item on replacing prompted-JSON parsing with a provider-native structured-output contract.

## Next Module

Planning's own Production Evolution table names the natural next step: **plan-driven execution (v0.2 of this module)** — making `RuntimeEngine` actually work through a `Plan`'s steps instead of only recording it, with a defined fallback to reactive behavior for anything the plan didn't anticipate. Beyond that, the same enterprise-extension track that motivated Sandbox and this module points at a **Workflow** layer: composing multiple steps, requests, or even multiple specialized agents against a persisted, resumable process, at which point "Planning" and "Multi-step Execution" (this module's reactive loop, largely unchanged since module 03) stop being separate concerns and become layers of one orchestration system.
