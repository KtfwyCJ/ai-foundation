# 09 — The CLI Chat Client

*Internal onboarding doc — AI Platform, reference client (`ai_platform/client/`)*

## 1. Executive Summary

Every prior module was server-side: something the Gateway composes or exposes. `ai_platform/client/chat_cli.py` is the platform's first *consumer* — an interactive terminal chat loop that talks to `POST /v1/chat` exactly like `curl` does in the README, just with a REPL wrapped around it and a `conversation_id` managed automatically. It ships as a console script (`ai-platform-chat`, via `[project.scripts]` in `pyproject.toml`), installed alongside the server by the same `pip install -e .`.

Its main engineering point isn't the REPL loop — it's a boundary decision: this file imports nothing from `ai_platform.api`, `ai_platform.runtime`, or any other server module. It only speaks HTTP and JSON, over the standard library's `urllib`. That's deliberate, and it's what this tutorial is really about.

## 2. The Problem

Every module before this one made the *platform* more capable. None of them made the platform easier to actually *use*. A developer wanting to have a conversation with it had to hand-write a `curl` command per turn, manually track and repeat a `conversation_id`, and manually parse `{"error": ..., "detail": ...}` JSON out of a failed response. That's a fine way to verify an API works; it's a bad way to actually talk to it.

## 3. Motivation

Every real API product ships a reference client alongside its server — not because the server needs it, but because a raw HTTP contract is a poor user experience by itself. The client's job is narrow: turn "compose a JSON body, set headers, POST it, parse the response" into "type a message, see a reply." Nothing about that job requires reaching into the server's internals — the whole point of the Gateway's `ChatRequest`/`ChatResponse` contract (module 01) was to make the HTTP boundary the *only* thing a caller needs to know about. This module is proof that boundary actually holds: a working client was built against it without importing a single line of server code.

## 4. Responsibilities

**The CLI client should:**
- Send one HTTP request per turn to `POST /v1/chat`, exactly matching the contract in `ai_platform/common/schemas.py` (`ChatRequest`/`ChatResponse`) — as an external caller would, not by importing those types
- Generate and reuse one `conversation_id` for the life of a session, so Memory (module 05) keeps the conversation coherent across turns without the user managing it
- Translate the platform's `{"error": "...", "detail": "..."}` error shape (module 01's error handler) into a one-line, readable message instead of a stack trace
- Run with zero non-stdlib dependencies, so it imposes no additional install cost beyond what running the server already requires

**The CLI client should NOT:**
- Import from `ai_platform.api`, `ai_platform.runtime`, `ai_platform.providers`, or any other server-side module — it's a caller, and should be buildable by someone who only has the README's HTTP contract, not the source code
- Reimplement retry, backoff, or rate-limit handling — those are the Gateway's and Anthropic's concerns; the client surfaces whatever error comes back and lets the user decide what to do
- Persist chat history to disk, support multiple concurrent sessions, or manage multiple API keys — real client features, deliberately out of scope for a first, minimal reference implementation (see Production Evolution)

## 5. Architecture

```
   Terminal (stdin/stdout)
          │
          ▼
   ┌───────────────────┐
   │  chat_cli.main()        │   REPL loop: read a line, build a
   │  ai_platform/client/    │   ChatRequest-shaped dict, call
   └─────────┬──────────┘   send_message(), print the reply
             │
             ▼
   ┌───────────────────┐
   │  send_message()         │   urllib.request — one POST per turn,
   │  (same file)             │   raises GatewayError with the
   └─────────┬──────────┘   platform's own error/detail on failure
             │  HTTP: POST /v1/chat
             ▼
   ┌───────────────────┐
   │  Gateway                 │   ai_platform/api/ — everything from
   │  (a separate process)    │   module 01 onward, unchanged
   └───────────────────┘
```

Upstream: nothing — this is a leaf, a terminal application a human runs directly. Downstream: the Gateway, reached only over HTTP, the same way any external caller (a `curl` command, a future web UI, a third-party integration) would reach it. There is no "sideways" relationship to the rest of `ai_platform/` — that's the entire point: this module proves the platform is usable by something that only knows its public HTTP contract.

## 6. Request Flow

1. **`main()` starts a session**: generates one `conversation_id = str(uuid.uuid4())` for the whole run, and prints it so the user can correlate it with server-side logs/traces if needed.
2. **The REPL reads a line** from `input("You: ")`. Empty input is ignored; `exit`/`quit` (or `Ctrl+C`/EOF) ends the session.
3. **The first turn only**, if `--system` was passed, a `{"role": "system", ...}` message is prepended — sent once, because Memory (module 05) persists it into the conversation's stored history; resending it every turn would duplicate it there.
4. **`send_message()` builds the exact `ChatRequest` shape** (`messages`, `model`, `conversation_id`) as JSON, POSTs it with `Authorization: Bearer <api-key>`, and either returns the parsed `ChatResponse` body or raises `GatewayError` with the platform's own `error`/`detail` fields extracted from a non-2xx response.
5. **The loop prints `Assistant: {response['message']['content']}`** and goes back to step 2 — the `conversation_id` from step 1 is reused on every subsequent turn, so the user never has to resend prior history themselves (see the README's Usage Guide for the same behavior via raw `curl`).
6. **On a `GatewayError`** (e.g. the API key was wrong, or the Gateway is rate-limiting), the loop prints `[error] <ErrorType>: <detail>` and continues — one bad turn doesn't end the session.
7. **On a `urllib.error.URLError`** (the Gateway process isn't reachable at all — wrong `--url`, or it's not running), the loop prints a connection-specific message and continues, rather than crashing on the next `input()` call.

## 7. Design Decisions

**Why does this module use `urllib` (stdlib) instead of `httpx` or `requests`?**
`httpx` is already a dependency — but a *dev* one (`pyproject.toml`'s `[project.optional-dependencies].dev`, used only by `TestClient` in the Gateway's own tests). Making the client depend on it would mean a plain `pip install -e .` (no `[dev]` extra) wouldn't be enough to run the client that ships in the same package. `urllib` costs nothing extra to depend on and is entirely sufficient for "POST one JSON body, read one JSON response."

**Why does the client generate its own `conversation_id` instead of leaving it to the server?**
`ChatRequest.conversation_id` is caller-supplied by design (see module 05) — the server has no notion of "sessions," only of whatever id a caller sends. A CLI session maps naturally to one conversation, so generating one `uuid4()` per process run and reusing it every turn is the client's own, local decision, exactly mirroring what any other stateful caller (a future web UI keeping a session cookie, a Slack bot keying off a channel id) would do.

**Why send the system prompt only once, not on every turn?**
Because Memory persists whatever's newly sent on each successful turn (module 05's `messages[len(history):]` logic). If the client resent the system message every turn, each one would land in the stored history as a *separate* system turn, and the model would see N copies of the same instruction by the Nth turn — confusing at best. Sending it once, on the first turn, relies on Memory already having captured it for every turn after.

**Why raise a client-defined `GatewayError` instead of letting `urllib.error.HTTPError` propagate to the REPL loop?**
`HTTPError` only exposes a status code and a raw response body the caller has to know to `json.loads()` and unpack itself. `GatewayError` does that unpacking once, in `send_message()`, and carries a message already formatted from the platform's own `{"error", "detail"}` shape — the REPL loop's `except` clause becomes one line instead of repeating JSON-parsing logic inline.

**Why is there no test for the interactive loop (`main()`) itself, only for `send_message()`?**
`main()` is almost entirely I/O — `input()`, `print()`, a `while True` — and testing it meaningfully would mean mocking stdin/stdout and asserting on printed strings, which tends to produce brittle tests that break on wording changes without catching real bugs. `send_message()` is where the actual logic lives (request shape, error translation) and is fully unit-tested; `main()`'s correctness was instead verified by actually running the installed `ai-platform-chat` script against a live Gateway (see this module's own development — a scripted multi-turn session, and a scripted bad-API-key session), which is a stronger signal for a thin I/O wrapper than a mocked unit test would be.

## 8. Alternative Designs

| Alternative | Why not |
|---|---|
| **Import `RuntimeEngine` directly and skip HTTP entirely** | Would couple the client to the server's process and in-process dependency graph (needing an `AsyncAnthropic` client, a `ToolRegistry`, etc., wired up locally) — defeating the purpose of having a Gateway contract at all. A real client should only need network access to a running Gateway, exactly like any other caller. |
| **A web UI instead of (or before) a CLI** | Legitimate, and named as a future option in the brainstorming that led here — but a CLI is a smaller, dependency-free surface that proves the same HTTP-boundary point with far less code, and fits a terminal-first workflow the rest of this project's `curl`-based Usage Guide already assumes. |
| **`httpx`/`requests` for the HTTP calls** | Marginally nicer ergonomics (no manual `Request` object, built-in JSON helpers) — rejected only because it would add a hard runtime dependency for a feature the stdlib already handles adequately; revisit if the client grows features (connection pooling, streaming) `urllib` doesn't handle gracefully. |
| **Storing conversation history client-side and resending full history every turn (stateless mode)** | Would work without relying on the server's Memory module at all, but throws away exactly the feature module 05 was built to provide, and would need the client to reconstruct the same "what's new since last time" logic Runtime already owns server-side. |

## 9. Trade-offs

**Gained:** a genuinely usable way to have a multi-turn conversation with the platform from a terminal, installable with the rest of the package (`ai-platform-chat` after `pip install -e .`), and — more importantly for the platform's own design — concrete proof that the Gateway's HTTP contract is sufficient to build a real client against without any access to server internals.

**Cost:** the client is intentionally minimal — one session, one conversation, no persistence across CLI invocations (exit the process and the `conversation_id` and everything the server remembered under it, while still stored in `InMemoryStore`, has no client-side way to be resumed unless you already know the id), no streaming output (the whole reply prints at once, after the full round trip completes), and no way to switch models or systems mid-session without restarting.

## 10. Production Evolution

```
v0.1 (this module)
  one conversation per process run, generated conversation_id
  full-response printing (no token streaming)
  errors printed inline, session continues
  stdlib-only HTTP (urllib)
        │
        ▼
v0.2
  persist/resume sessions: save conversation_id (+ maybe local
    transcript) to disk, so `ai-platform-chat --resume <id>` works
  streaming output, once the Gateway itself exposes a streaming
    response mode (today it's request/response only)
  slash-commands in the REPL (/model, /system, /new) instead of
    fixed CLI flags for the whole session
        │
        ▼
Enterprise version
  multiple named profiles (different API keys / base URLs for
    different environments — local, staging, prod)
  scriptable/non-interactive mode (pipe a prompt in, get one reply
    out, exit — useful for shell scripts and CI smoke tests)
  structured output mode (--json) for programmatic consumption
        │
        ▼
Large-scale platform
  a real SDK (typed request/response models, retries with backoff,
    async client) that this CLI itself is refactored to sit on top
    of, the same way real provider SDKs (this platform's own
    `anthropic` dependency) separate "SDK" from "CLI built on the SDK"
```

The scaling challenge here is almost entirely UX, not architecture — the HTTP contract this client depends on (module 01's `ChatRequest`/`ChatResponse`) doesn't need to change for any of the above; what grows is how much convenience is layered on top of one HTTP call per turn.

## 11. Real-world Examples

- **The OpenAI CLI / `openai` Python package's CLI entry points** — a reference client shipped in the same package as the SDK, talking to the same public API surface any other integration would use.
- **`curl`/Postman as "reference clients"** — this module's `send_message()` is doing, in code, exactly what the README's `curl` examples do by hand: the fact that both work identically against the same endpoint is the real validation of the Gateway's contract.
- **`redis-cli`, `psql`** — the general pattern of "a database/service ships a thin interactive terminal client separate from the server binary," which this module follows for an HTTP API instead of a binary protocol.

## 12. Common Mistakes

- **Reaching into server internals "just this once" to avoid an HTTP round trip.** Defeats the purpose of having a client at all — if the client needs anything beyond the documented HTTP contract, that's a signal the contract itself is incomplete, not a reason to bypass it.
- **Resending full conversation history from the client instead of relying on `conversation_id`.** Ignores the Memory module's entire reason for existing and reintroduces exactly the "resend everything every time" burden module 05 was built to remove.
- **Letting a single bad turn (a network blip, a bad flag) crash the whole session.** A REPL that dies on the first hiccup is worse than useless for anything but a demo — this module's `except` blocks around each turn are what make a long interactive session resilient to a single failed request.
- **Adding a client-side retry loop "to be safe."** Retrying a request that already failed for a caller-side reason (bad API key, malformed input) just repeats the same failure; retries belong closer to genuinely transient failures (the Gateway's own provider-timeout handling), not bolted onto every client blindly.

## 13. Best Practices

- Build reference clients against the same public contract any other caller would use — never against internal server types, even when it's technically possible because the code lives in the same repo.
- Keep a CLI's runtime dependencies minimal; a thin HTTP-and-print tool rarely needs more than the standard library.
- Translate a server's structured error responses into clean, one-line messages at the client's boundary — don't make every call site re-parse the same JSON error shape.
- Let one failed operation degrade gracefully (print an error, keep the loop running) rather than terminating an entire interactive session.
- Verify a client by actually running it against a live server end-to-end (multi-turn session, a deliberately-broken session) — not only by unit-testing its internal request-building logic in isolation.

## 14. Interview Knowledge

**Must Know**
- Why a well-designed HTTP API should be usable by a client that has no access to server source code — the contract (request/response schema, auth, error shape) is the whole interface.
- The difference between a "reference client" (thin, ships with the API) and a full SDK (typed, retry-aware, often generated from an OpenAPI spec) — this module is the former, explicitly not yet the latter.
- Why session/conversation state belongs on the server (Memory) rather than being reconstructed by resending full history from every client.

**Good to Know**
- Trade-offs between minimal stdlib HTTP clients and richer libraries (`httpx`/`requests`): fewer dependencies vs. nicer ergonomics, connection pooling, and native async support.
- Why a CLI generating and reusing an id (`conversation_id`) locally is a common, legitimate pattern for mapping "one terminal session" onto "one stateful server-side resource," without the server needing any concept of a "session" itself.
- The UX difference between full-response and streaming output, and why streaming requires server-side support (SSE or chunked responses) the client alone can't add.

**Advanced**
- Designing a real SDK layer (typed models generated from an OpenAPI schema, retry/backoff policies, sync and async clients) that a CLI like this one would eventually be refactored to sit on top of.
- Multi-environment/profile management (dev/staging/prod credentials and base URLs) for a CLI tool used across more than one deployment of the same platform.
- Non-interactive/scriptable client design (pipe-in, single-shot, `--json` output) as a distinct use case from an interactive REPL, and how the same core `send_message()`-style function serves both.

## 15. Key Takeaways

1. This is the platform's first *consumer* module — everything before it made the server more capable; this makes the server easier to actually talk to, without changing it at all.
2. It imports nothing from any other `ai_platform` module — only `urllib`, `json`, `argparse`, `uuid` — proof that the Gateway's HTTP contract (module 01) is sufficient on its own to build a real client against.
3. One `conversation_id`, generated client-side and reused for the whole session, is what lets Memory (module 05) keep a terminal conversation coherent across turns — the client manages *which* conversation, the server manages *what's in it*.
4. The platform's structured `{"error", "detail"}` responses (module 01's error handler) are what make a one-line, readable `[error] ...` message possible here instead of a client-side stack trace.
5. Verification for this module leaned on actually running the installed console script against a live Gateway (a multi-turn session, a deliberately bad API key) rather than unit tests alone — the right call for a thin I/O wrapper whose real risk is "does it work end to end," not "is the internal logic correct in isolation."

## Further Reading

1. `urllib.request` — [official docs](https://docs.python.org/3/library/urllib.request.html) — the stdlib HTTP client this module is built on.
2. OpenAI Python library — [GitHub](https://github.com/openai/openai-python) — a real-world example of a client/SDK shipped alongside (and versioned with) the API it calls.
3. The Twelve-Factor App — [https://12factor.net/](https://12factor.net/) (already referenced in the Deployment tutorial) — relevant here too: a client should treat the server as a network dependency reached over a stable contract, never as code to import.
4. Click — [documentation](https://click.palletsprojects.com/) — a popular library for richer Python CLIs (subcommands, better argument parsing), worth adopting if this client's argument surface grows past what `argparse` handles comfortably.

## Next Module

This tutorial closes a different loop than modules 01–08: those built the platform outward (Gateway → ... → Deployment); this one builds *on top of* it, proving the finished platform is directly usable. Natural next steps in the same spirit (see this module's own Production Evolution) are session persistence/resume, or a second consumer — most usefully a **second tool** (`ai_platform/tools/`) exercised through this same CLI, since a client that can drive a real multi-tool conversation is a stronger end-to-end demonstration than the current single-tool (`calculator`) example.
