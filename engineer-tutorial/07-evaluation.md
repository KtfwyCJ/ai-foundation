# 07 — The Evaluation Module

*Internal onboarding doc — AI Platform, Evaluation component (`ai_platform/evaluation/`)*

## 1. Executive Summary

Every prior module made Runtime more capable (tools, memory) or more observable (tracing) — none of them answer whether Runtime's *answers* are any good. A prompt change, a model swap, a tool edit, or a provider upgrade can silently make responses worse, and nothing in this platform would notice until a human did.

Evaluation is a harness that runs a fixed set of `EvalCase`s (an input conversation plus an expectation) through any `RuntimeClient`, grades each response with a `Grader`, and produces a per-case `EvalResult` plus an aggregate `EvalSummary` (pass/fail/error counts and a pass rate). `ContainsGrader` is the v0.1 grader — a deterministic substring check — the same "ship the one example implementation" restraint this platform used for `AnthropicProvider` and `CalculatorTool`. The `Grader` protocol is what lets an LLM-as-judge grader be added later without touching `EvalRunner`.

## 2. The Problem

An LLM-backed system's correctness isn't binary in the way a typical unit-tested function's is — the same prompt can produce different wording across model versions, and "correct" often means "contains the right fact" or "satisfies a rubric," not "equals this exact string." Without a repeatable way to check that:

- **Regressions are invisible.** Nothing catches a prompt or tool change that silently degrades answer quality — the existing test suite verifies *that Runtime calls the right things in the right order* (via `FakeModelProvider`), never *whether a real model's answer is actually correct*.
- **There's no way to compare two configurations.** "Is `claude-opus-4-8` better than `claude-sonnet-5` for this platform's use case?" or "did adding this tool description change behavior?" has no answer without running the same test set against both and comparing.
- **Tracing data (module 06) has nothing to feed.** Latency and token counts are now recorded, but "was this fast response also a *correct* one" requires pairing trace data with a quality judgment — which didn't exist until this module.

## 3. Motivation

Enterprise ML/LLM systems separate three kinds of testing that are often conflated: unit tests (does the code behave correctly given a scripted dependency — what `tests/runtime/test_engine.py` already does with `FakeModelProvider`), integration tests (does the code work against a real backend at all), and **evaluation** (given a real or realistic model, are the *answers* good). Evaluation is the only one of the three that's about output quality rather than code correctness, and it needs its own harness because grading an LLM response requires different tooling — a `Grader` — than an `assert` does.

Building `EvalRunner` against the `RuntimeClient` interface (not the concrete `RuntimeEngine`) is the same dependency-inversion decision the Gateway made from day one: it means eval cases can run against a fake in a fast unit test, or against a fully wired `RuntimeEngine` with a real Anthropic key, without the harness caring which.

## 4. Responsibilities

**Evaluation should:**
- Define what a test case is (`EvalCase` — `types.py`): an input conversation, a model, and an `expected` string whose meaning is up to whichever grader interprets it
- Define the `Grader` protocol (`interfaces.py`): a single `grade(response, case) -> GradeResult` method
- Ship one real, deterministic grader (`ContainsGrader` — `graders.py`) as a working v0.1 example
- Run a list of cases through a `RuntimeClient` and grade each response (`EvalRunner.run()` — `runner.py`), recording a request-level failure as an **error**, distinct from a **failed grade**
- Aggregate results into pass/fail/error counts and a pass rate (`summarize()`)

**Evaluation should NOT:**
- Decide *which* concrete `RuntimeClient` to run against — that's the caller's job, exactly like `RuntimeEngine` never decides which `ModelProvider` it's given
- Own a golden dataset of "the platform's real eval suite" — `EvalCase`s are a data shape, not a fixed catalog; a real suite lives wherever the team maintaining it wants (a JSON/YAML file, a dataset service), loaded and passed to `EvalRunner`
- Implement an LLM-as-judge grader in v0.1 — the `Grader` protocol is designed for it, but building one without a concrete rubric and a real need is exactly the kind of speculative work this platform has repeatedly deferred (see the Tool Registry's single hardcoded tool, the Provider layer's single vendor)
- Gate deploys or fail CI itself — `EvalSummary.pass_rate` is a number a CI step or human can act on; enforcing a threshold is a policy decision that belongs to whatever calls this harness, not the harness itself

## 5. Architecture

```
        caller (test, script, CI job)
              │  builds list[EvalCase], picks a Grader
              ▼
      ┌─────────────────────────────┐
      │   EvalRunner                    │   ai_platform/evaluation/runner.py
      │   run(cases) -> list[EvalResult]│
      └──────┬───────────────┬────────┘
             │ calls             │ calls
             ▼                   ▼
   ┌───────────────────┐   ┌─────────────────────┐
   │ RuntimeClient          │   │ Grader (Protocol)       │
   │ (Gateway's interface)  │   │ grade(response, case)   │
   │ handle_chat(request)   │   └──────────┬──────────────┘
   └───────────────────┘              │ implemented by
                                       ▼
                             ┌─────────────────────┐
                             │ ContainsGrader           │
                             └─────────────────────┘

      results: list[EvalResult]  ──►  summarize()  ──►  EvalSummary
```

Upstream: nothing depends on Evaluation — it's a leaf tool a developer, script, or CI job invokes directly, not something the Gateway or Runtime ever calls at request time. Downstream: `EvalRunner` depends on `RuntimeClient` (so it can run against a real wired `RuntimeEngine` or a fake identically) and `Grader` (so grading strategy is swappable). Sideways: Evaluation never imports `RuntimeEngine`, `ModelProvider`, `ToolRegistry`, or `Tracer` directly — it only knows the `ChatRequest`/`ChatResponse` contract and the `RuntimeClient` Protocol, exactly the boundary the Gateway itself respects.

## 6. Request Flow

Walking through `EvalRunner.run()` on a two-case suite:

1. **Caller builds `EvalCase`s** — e.g. `EvalCase(id="calc-1", messages=[...], expected="5")` — and picks a `Grader`, e.g. `ContainsGrader()`.
2. **`EvalRunner(runtime, grader).run(cases)`** iterates the cases, calling `self._run_one(case)` for each.
3. **`_run_one`** builds a `ChatRequest(messages=case.messages, model=case.model)` and calls `await self._runtime.handle_chat(request)` — the exact same call the Gateway's `/v1/chat` route makes.
4. **If the call raises** (a `ProviderTimeoutError`, a `RuntimeToolLoopExceededError`, anything) — `_run_one` catches it and returns `EvalResult(case_id=case.id, error=str(exc))`, with `grade=None`. This case is recorded as **broken**, not **wrong**.
5. **If the call succeeds**, `_run_one` calls `await self._grader.grade(response, case)`. `ContainsGrader` checks whether `case.expected` (case-insensitively) appears in the response's text content, and returns a `GradeResult(passed, score, detail)`.
6. **`_run_one`** returns `EvalResult(case_id, response_text, grade)` — response text is best-effort (`None` if the final message was a tool-result block list rather than plain text, since that shape doesn't apply to a grader expecting text).
7. **After all cases run**, `summarize(results)` computes `total`, `passed`, `failed` (= `total - passed - errored`), `errored`, and `pass_rate = passed / total`.
8. **The caller (a test, a script, a CI job) acts on the summary** — print it, assert `pass_rate >= threshold`, or diff it against yesterday's run. Evaluation's job stops at producing the numbers.

## 7. Design Decisions

**Why does a raised exception become an `EvalResult.error`, not a failed `GradeResult`?**
Because they're different failure modes that need different responses: a failed grade means the system produced an answer and it was wrong (a quality problem, likely fixed by prompt/tool/model changes); an error means the system didn't produce an answer at all (an infrastructure problem — a timeout, an exceeded tool loop, a bad request). Collapsing both into "failed" would hide which one happened, and a team acting on eval results needs to know whether to look at prompt quality or system reliability.

**Why does `EvalRunner` depend on `RuntimeClient`, not `RuntimeEngine`?**
Same reasoning the Gateway's own routes use (`Depends(get_runtime_client)` returns a `RuntimeClient`): depending on the interface means `EvalRunner` can be unit-tested against a scripted `FakeRuntimeClient` (as this module's own tests do) without spinning up a real `RuntimeEngine`, a `ToolRegistry`, or a real Anthropic key — and the exact same `EvalRunner` code runs unchanged against a fully wired production Runtime.

**Why is `EvalCase.expected` an untyped `str` instead of a structured expectation (e.g. `{"contains": "5"}` or a rubric object)?**
There's exactly one grader today, and its notion of "expected" is "a substring to find." A structured expectation schema that has to accommodate substring checks, exact match, numeric tolerance, *and* an LLM-judge rubric simultaneously would be designed against three graders that don't exist yet. Keeping `expected: str` and letting each `Grader` interpret it its own way is the same "don't design ahead of the second real use case" discipline the Tool Registry tutorial named explicitly.

**Why is `summarize()` a standalone function instead of a method on `EvalRunner` or `EvalResult`?**
It only needs a `list[EvalResult]` — no runner state, no per-result state — so making it a free function avoids implying a dependency that doesn't exist. This is the same reasoning that kept `ToolRegistry.definitions()` a plain method rather than needing external state: a function's signature should reflect exactly what it depends on.

**Why does `ContainsGrader` fall back to `None`/empty string when `response.message.content` is a block list rather than raising?**
A tool-calling turn's final answer is normally still plain text by the time the tool loop ends (per the Runtime tutorial's request flow), but `ChatMessage.content` is typed as `str | list[ContentBlock]` and a grader has to handle the type it's declared to accept. Treating a non-text response as "doesn't contain the expected string" (i.e., a normal failed grade) is more useful than crashing the whole eval run over one malformed case — consistent with keeping `RuntimeEngine`'s own error handling narrow and intentional rather than defensively broad.

## 8. Alternative Designs

| Alternative | Why not |
|---|---|
| **Plain `pytest` test functions calling a real model, asserting on substrings** | Works for a handful of cases, but doesn't scale to "run 200 cases and see a pass rate," doesn't separate infrastructure errors from wrong answers, and ties eval results to pytest's pass/fail semantics instead of a queryable `EvalSummary` a dashboard or CI gate could consume. |
| **A single `evaluate(case) -> bool` function instead of `EvalCase`/`Grader`/`EvalRunner` as separate types** | Collapses "what to check," "how to check it," and "run many of them" into one function signature — exactly the coupling this platform's Protocol-based DI pattern (`ModelProvider`, `ToolRegistry`, `MemoryStore`, `Tracer`) has avoided everywhere else, for the same reason: swapping the grading strategy would mean editing the function instead of registering a new class. |
| **Build the LLM-as-judge grader now, since it's "the real one"** | An LLM judge needs a rubric, its own model call (via `ModelProvider`, reusable from this platform), and a scoring scheme — real design work that deserves its own deliberate pass once there's a concrete rubric to build against, not a placeholder implementation added for completeness. |
| **Have `EvalRunner` assert/raise on failure itself** | Would make the harness opinionated about pass/fail policy (what threshold matters, whether an error counts as failure) — a decision that varies by caller (a CI gate wants a hard threshold; an exploratory script just wants the numbers). `EvalSummary` reports the facts; enforcing a policy on them is left to the caller, same boundary the Gateway's error handler keeps between "what happened" and "what HTTP status that means." |

## 9. Trade-offs

**Gained:** a repeatable way to run a fixed set of prompts through Runtime and get a pass rate, independent of which `RuntimeClient` (fake or real) is behind the call. Regression testing for output *quality* — not just code correctness — now has a home, and it's built on the same `RuntimeClient`/`ChatRequest`/`ChatResponse` contract every other module already respects, so nothing about Runtime or the Gateway had to change to support it.

**Cost:** `ContainsGrader` is a genuinely weak signal — brittle substring matching can't tell "correct but rephrased" from "wrong," and there's no case selection/curation logic here (that's the caller's job, and a real golden dataset is a meaningful investment this module deliberately doesn't attempt to own). `EvalRunner` also runs cases strictly sequentially, so a large suite against a real model will be slow — a deliberate v0.1 simplicity trade-off, not an oversight.

## 10. Production Evolution

```
v0.1 (this module)
  one deterministic grader (ContainsGrader)
  sequential case execution
  no persisted eval history — results only exist for the run that produced them
  no dataset management — EvalCases are just data the caller supplies
        │
        ▼
v0.2
  LLM-as-judge grader (uses ModelProvider to score responses against
    a rubric, not just a substring)
  parallel case execution (asyncio.gather over cases, bounded by a
    concurrency limit)
  persisted eval runs (store EvalSummary + results over time, so a
    pass-rate trend is visible, not just the latest run)
        │
        ▼
Enterprise version
  a real golden dataset service (versioned EvalCase sets, curated and
    reviewed like a test suite, not ad-hoc lists in code)
  CI-gated evaluation (a pull request that drops pass_rate below a
    threshold fails the build, the same way a broken unit test would)
  per-category/per-tag breakdowns (pass rate by task type, not just
    one aggregate number)
        │
        ▼
Large-scale platform
  online evaluation (sampling a percentage of real production traffic
    through the same Grader machinery, not just a fixed offline set)
  A/B comparison harness: run the same cases through two Runtime
    configurations (model, prompt, tool set) and diff the summaries
  human-in-the-loop review queues for cases graders disagree on or
    flag as low-confidence
```

The scaling challenge here is grading quality and dataset governance, not the harness shape — `EvalCase`/`Grader`/`EvalRunner` barely change from v0.1 to large-scale; what grows is how good the grader is (substring → LLM judge → human review) and how seriously the dataset itself is maintained (ad-hoc list → versioned, curated corpus).

## 11. Real-world Examples

- **OpenAI Evals** — an open framework for exactly this: a registry of eval cases and pluggable graders (including model-graded evals), run against any completion-producing system.
- **Anthropic's own eval tooling and public model evals** — the same case-plus-grader shape, with graders ranging from exact-match to model-based grading, at the scale of full model release evaluation.
- **Langfuse / LangSmith evaluation features** — both layer eval harnesses directly on top of their tracing data (this platform's module 06), scoring traced LLM calls against datasets — the same "tracing feeds evaluation" relationship this module's tutorial ordering reflects.
- **Ragas** — a popular evaluation library specifically for RAG pipelines, illustrating how domain-specific grading logic (faithfulness, relevance) plugs into the same "case in, graded result out" shape as a generic `Grader`.

## 12. Common Mistakes

- **Conflating a request-level error with a failed grade.** A timeout and a wrong answer are different problems requiring different fixes — this module's `EvalResult.error`/`EvalResult.grade` split exists specifically to keep them apart.
- **Grading with exact string equality instead of a tolerant check.** LLM outputs vary in phrasing even when correct; `ContainsGrader`'s substring/case-insensitive check is itself a minimal step in that direction, and a real suite needs graders tolerant of the specific kind of variation its task produces (numeric tolerance, semantic similarity, structured-output parsing).
- **Treating a single eval run's pass rate as a permanent verdict.** Without persisted history (a named v0.2 gap here), there's no way to tell if a pass rate is trending up or down — a snapshot in time can mislead if treated as a final answer.
- **Building an LLM-as-judge grader before there's a rubric.** "Have a model grade it" sounds like a solution but is only as good as the rubric behind it — a vague or missing rubric produces a confidently wrong grader, arguably worse than an honest, narrow deterministic check.
- **Skipping evaluation entirely because "the unit tests pass."** Unit tests (this platform's `FakeModelProvider`-based tests) verify *code* correctness against scripted responses; they cannot catch a real model producing worse answers after a prompt or model change — that's precisely the gap evaluation exists to close.

## 13. Best Practices

- Keep "what to check" (`EvalCase`), "how to check it" (`Grader`), and "run many of them" (`EvalRunner`) as separate, independently testable pieces.
- Distinguish infrastructure failures from wrong answers in eval results — they demand different fixes.
- Depend on the same client-facing interface (`RuntimeClient`) production code depends on, so eval results reflect what real callers actually experience.
- Report evaluation as data (`EvalSummary`), and let the caller decide what policy — a CI threshold, a dashboard, a human review — to apply to it.
- Don't build a grading strategy more sophisticated than the current dataset and use case justify; start deterministic, add model-graded evaluation once there's a rubric worth encoding.

## 14. Knowledge

**Must Know**
- The difference between unit testing (code correctness against scripted dependencies), integration testing (does it work against a real backend), and evaluation (is the *output* good) — and why LLM systems specifically need the third category that traditional software often doesn't.
- Why an eval harness should be built against the same client interface production code uses, rather than a parallel, divergent path.
- Why "the request failed" and "the request succeeded but was wrong" need to be tracked as separate outcomes.

**Good to Know**
- What LLM-as-judge grading is, its main failure mode (a judge is only as reliable as its rubric and its own model's judgment), and why it's typically introduced only after deterministic checks prove insufficient.
- Why eval datasets need governance (versioning, review) at scale — the same rigor a unit test suite gets, applied to prompts and expected behaviors instead of code paths.
- The relationship between tracing (module 06) and evaluation: tracing tells you what happened; evaluation tells you whether what happened was good, and the two are increasingly combined in real observability platforms.

**Advanced**
- Designing graders for structured or tool-using outputs (not just free text) — e.g. did the model call the *correct* tool with *correct* arguments, not just "does the final text contain X."
- Online evaluation: continuously sampling live production traffic through the same grading machinery, versus a fixed offline dataset run periodically.
- Statistical considerations in eval suite size and grader noise — how many cases are needed before a pass-rate delta between two configurations is meaningful rather than noise.

## 15. Key Takeaways

1. Evaluation answers a question none of the prior modules could: not "did the system run correctly" but "was the answer any good" — a distinct concern from both unit testing and tracing.
2. `EvalCase` (input + expectation), `Grader` (protocol for judging a response), and `EvalRunner` (orchestrates running cases and grading them) are three separate, independently swappable pieces — the same Protocol-based DI shape as every prior module.
3. A request that raises is recorded as an `error`, never collapsed into a failed `grade` — infrastructure breakage and wrong answers are different problems that need different fixes.
4. `EvalRunner` depends on `RuntimeClient`, not `RuntimeEngine` — the exact interface the Gateway itself depends on — so the same harness runs unchanged against a fake in a fast test or a fully wired production Runtime.
5. `ContainsGrader` is a deliberately minimal v0.1 grader, the same restraint this platform applied to shipping one `ModelProvider` and one `Tool` before generalizing — the `Grader` protocol is what makes an LLM-as-judge grader an additive class later, not a rewrite.

## Further Reading

1. OpenAI — [Evals framework](https://github.com/openai/evals) (GitHub) — an open, widely-used implementation of the case-plus-grader pattern this module's `EvalCase`/`Grader`/`EvalRunner` mirrors at a larger scale.
2. Anthropic — [Building evals](https://docs.anthropic.com/en/docs/test-and-evaluate/develop-tests) (official docs) — practical guidance on designing eval cases and grading strategies for Claude specifically.
3. Ragas — [Documentation](https://docs.ragas.io/) — a domain-specific (RAG) evaluation library, useful as an example of task-specific graders layered on a generic harness shape.
4. Langfuse — [Evaluation](https://langfuse.com/docs/scores/overview) — shows how tracing data (module 06's concern) and evaluation scores (this module's concern) combine in a real observability product.
5. Hugging Face — [Evaluate library](https://huggingface.co/docs/evaluate/index) — a broader survey of metric/grading approaches (exact match, BLEU/ROUGE, model-based) relevant background for choosing a second grader beyond `ContainsGrader`.

## Next Module

**Deployment** (packaging, containerization, CI). Every module up to this one has been implementable and testable entirely in-process — there is still no way to actually *run* this platform outside a developer's local `uvicorn --reload`. Deployment is where the platform gets a `Dockerfile` (a reproducible runtime image), a way to run it alongside a real dependency (a `docker-compose.yml`, if a shared backend like Redis is introduced for Memory/rate-limiting at that point), and a CI workflow that runs the test suite (now covering Gateway, Providers, Runtime, Tools, Memory, Tracing, and Evaluation) on every change — turning "code that works on one machine" into "an artifact anyone can run the same way."
