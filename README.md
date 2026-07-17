# ai-foundation

A production-inspired AI platform framework, built module by module: a FastAPI
Gateway in front of a provider-agnostic model layer, an agentic Runtime with
tool-calling and persisted memory, span-based tracing, an offline evaluation
harness, and a containerized, CI-tested deployment path.

Every module follows the same shape: a `Protocol` interface the rest of the
platform depends on, one real v0.1 implementation behind it, and a full
engineering write-up in [`engineer-tutorial/`](engineer-tutorial/) covering
*why* it's built the way it is — architecture, design decisions, trade-offs,
and production evolution — not just what the code does.

## Architecture

```
        Client
          │
          ▼
   ┌─────────────┐   auth, rate limiting, routing
   │   Gateway        │   ai_platform/api/
   └──────┬───────┘
          │  RuntimeClient
          ▼
   ┌─────────────┐   provider + tools + memory + tracing composition
   │   Runtime        │   ai_platform/runtime/
   └──┬───┬───┬────┘
      │     │     │
      ▼     ▼     ▼
  Provider  Tools  Memory        + Tracer (spans on every provider/tool call)
  ai_platform/    ai_platform/  ai_platform/    ai_platform/
  providers/      tools/         memory/          tracing/

   Evaluation (ai_platform/evaluation/) runs EvalCases through any
   RuntimeClient and grades the responses — independent of the request path.
```

## How It Works

A single `POST /v1/chat` request flows through four layers. Each is
independently unit-tested against fakes (no real model calls needed to test
the platform's own logic) and has its own tutorial — this is the short
version.

1. **Gateway** (`ai_platform/api/`) — the only HTTP-facing layer. Validates
   the `Authorization: Bearer <key>` header against `AI_PLATFORM_API_KEYS`,
   enforces a per-key rate limit, and hands the request to Runtime through a
   `RuntimeClient` interface. The Gateway never knows which model provider,
   which tools, or which storage backend actually handle the request — it
   only knows the request/response shape (`ChatRequest`/`ChatResponse`).
2. **Runtime** (`ai_platform/runtime/`) — the orchestrator, and the only
   module that talks to everything else. If a `conversation_id` was given,
   it loads prior history first. It calls the model provider; if the model
   asks to use a tool, Runtime executes it and calls the model again — up
   to 5 iterations — before returning a final, non-tool answer. Every
   provider call and tool execution is timed and recorded as a trace span
   along the way, and once the request succeeds, any new turns (including a
   full tool exchange) are persisted if a `conversation_id` was given.
3. **Provider** (`ai_platform/providers/`) — translates the platform's
   generic `ChatMessage`/`ToolDefinition` types into Anthropic's actual
   request/response format and back, and maps Anthropic SDK exceptions onto
   typed platform errors (`ProviderAuthError`, `ProviderRateLimitError`,
   `ProviderTimeoutError`, ...) that the Gateway already knows how to turn
   into the right HTTP status — adding a second provider later means adding
   a new class here, not touching Runtime or the Gateway.
4. **Tools** (`ai_platform/tools/`) — a registry the model can call into
   mid-conversation (today: `calculator`). The registry only answers "what
   tools exist" and "how do I run one by name" — Runtime alone decides
   *when* to call one and how many times to loop.

None of this is visible to a caller: you send messages, you get an answer.
But it's *why* a malformed or unauthenticated request never reaches
Anthropic, why a tool call is invisible in the response but visible in a
trace span, and why a conversation survives across separate HTTP requests
without you ever resending its history. See each module's write-up in
[`engineer-tutorial/`](engineer-tutorial/) for the full design rationale —
including the alternatives considered and why they were rejected.

## Modules

| Module | Path | Tutorial |
|---|---|---|
| Common | `ai_platform/common/` | shared config, errors, schemas, interfaces |
| Gateway | `ai_platform/api/` | [01-gateway.md](engineer-tutorial/01-gateway.md) |
| Provider layer | `ai_platform/providers/` | [02-provider-layer.md](engineer-tutorial/02-provider-layer.md) |
| Runtime | `ai_platform/runtime/` | [03-runtime.md](engineer-tutorial/03-runtime.md) |
| Tool Registry | `ai_platform/tools/` | [04-tool-registry.md](engineer-tutorial/04-tool-registry.md) |
| Memory | `ai_platform/memory/` | [05-memory.md](engineer-tutorial/05-memory.md) |
| Tracing | `ai_platform/tracing/` | [06-tracing.md](engineer-tutorial/06-tracing.md) |
| Evaluation | `ai_platform/evaluation/` | [07-evaluation.md](engineer-tutorial/07-evaluation.md) |
| Deployment | `Dockerfile`, `docker-compose.yml`, `.github/workflows/` | [08-deployment.md](engineer-tutorial/08-deployment.md) |
| CLI Client | `ai_platform/client/` | [09-cli-client.md](engineer-tutorial/09-cli-client.md) |
| Sandbox | `ai_platform/sandbox/` | [10-sandbox.md](engineer-tutorial/10-sandbox.md) |
| Planning | `ai_platform/planning/` | [11-planning.md](engineer-tutorial/11-planning.md) |

## Quickstart

```bash
pip install -e ".[dev]"
```

Set a real Anthropic API key before starting the server — the `/v1/chat`
route calls the real Anthropic API through `ai_platform/providers/`, so it
needs credentials:

```bash
echo "AI_PLATFORM_ANTHROPIC_API_KEY=sk-ant-..." >> .env
```

(`Settings` in `ai_platform/common/config.py` auto-loads `.env`; you can
instead `export AI_PLATFORM_ANTHROPIC_API_KEY=sk-ant-...` in your shell.
Skipping this step doesn't hang or crash the server — every request to
`/v1/chat` returns a clean `500 {"error": "ProviderAuthError", ...}` instead.)

```bash
uvicorn ai_platform.api.app:app --reload
```

```bash
curl -X POST localhost:8000/v1/chat \
  -H "Authorization: Bearer dev-local-key" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "hello"}]}'
```

Configuration is environment-driven (`ai_platform/common/config.py`), prefixed
`AI_PLATFORM_` — e.g. `AI_PLATFORM_ANTHROPIC_API_KEY`, `AI_PLATFORM_API_KEYS`
(comma-separated keys the Gateway itself accepts via `Authorization: Bearer`,
independent of the Anthropic key above).

## API Docs (Swagger / OpenAPI)

FastAPI generates interactive API docs for free from the route definitions
in `ai_platform/api/routes/` — no extra setup. With the server running:

- **Swagger UI** — `http://localhost:8000/docs` — try requests directly in
  the browser (click "Authorize" and enter `dev-local-key` — or whichever
  key is in your `AI_PLATFORM_API_KEYS` — to call `/v1/chat` from the UI)
- **ReDoc** — `http://localhost:8000/redoc` — a read-only, more
  documentation-style view of the same schema
- **Raw schema** — `http://localhost:8000/openapi.json`

These reflect the `ChatRequest`/`ChatResponse` Pydantic models in
`ai_platform/common/schemas.py` directly, so the docs never drift from the
actual request/response shape.

## Usage Guide

Everything below is exercised against the real, running Gateway — not just
unit tests — to keep this section accurate.

### Authentication

Every request needs an `Authorization: Bearer <api-key>` header. Valid keys
come from `AI_PLATFORM_API_KEYS` (comma-separated; default `dev-local-key`).
A missing or invalid key returns `401`.

### Send a message

```bash
curl -X POST localhost:8000/v1/chat \
  -H "Authorization: Bearer dev-local-key" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "hello"}]}'
```

```json
{"message": {"role": "assistant", "content": "Hi there! How can I help you today?"}, "model": "claude-sonnet-5"}
```

### Choose a model

Add `"model": "claude-opus-4-8"` (or any valid Claude model id) to the
request body. Defaults to `claude-sonnet-5` if omitted.

### Steer behavior with a system prompt

Add a message with `"role": "system"` — it's extracted and sent to Anthropic
as a system prompt, not as part of the conversation turns:

```bash
curl -X POST localhost:8000/v1/chat \
  -H "Authorization: Bearer dev-local-key" -H "Content-Type: application/json" \
  -d '{"messages": [
        {"role": "system", "content": "Reply in at most five words."},
        {"role": "user", "content": "What is the capital of France?"}
      ]}'
```

### Multi-turn conversations

Pass a `conversation_id` and send only *that turn's* new message — prior
history is loaded and prepended automatically, so you never resend it:

```bash
# Turn 1
curl -X POST localhost:8000/v1/chat -H "Authorization: Bearer dev-local-key" -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "My favorite number is 42."}], "conversation_id": "session-abc"}'

# Turn 2 — a separate HTTP request, same conversation_id, no need to repeat turn 1
curl -X POST localhost:8000/v1/chat -H "Authorization: Bearer dev-local-key" -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What is my favorite number?"}], "conversation_id": "session-abc"}'
# → "Your favorite number is 42."
```

Omit `conversation_id` entirely for a stateless, one-off request — nothing
is persisted and no history is loaded.

### Tool use (automatic)

A `calculator` tool is registered by default (`ai_platform/tools/builtin.py`).
You never call it directly — the model decides to use it, Runtime executes
it, and only the final answer comes back to you:

```bash
curl -X POST localhost:8000/v1/chat -H "Authorization: Bearer dev-local-key" -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What is 847 * 213?"}]}'
# → "847 × 213 = 180,411" (computed by the tool, not guessed by the model)
```

### Rate limits

Default: 60 requests per 60-second window, per API key
(`AI_PLATFORM_RATE_LIMIT_REQUESTS` / `AI_PLATFORM_RATE_LIMIT_WINDOW_SECONDS`).
Exceeding it returns `429`.

### Errors

Every error response is JSON: `{"error": "<ErrorType>", "detail": "..."}` —
never a raw stack trace.

| Status | Error | Meaning |
|---|---|---|
| 401 | `AuthenticationError` | Missing or invalid `Authorization` header |
| 422 | `ValidationError` | Malformed request body (e.g. empty `messages`) |
| 429 | `RateLimitExceededError` | Too many requests for this API key in the current window |
| 500 | `ProviderAuthError` | The platform's own Anthropic credentials are missing/invalid — not the caller's fault |
| 502 | `ProviderError` | Anthropic rejected the request for another reason (e.g. unknown model id) |
| 503 | `ProviderRateLimitError` | Anthropic itself is rate-limiting this platform |
| 503 | `RuntimeToolLoopExceededError` | The model never converged on a final answer within 5 tool-call iterations |
| 504 | `ProviderTimeoutError` | Anthropic didn't respond in time |

### What's not exposed over HTTP

- **Tracing** (`ai_platform/tracing/`) — records a span (timing, tokens,
  errors) for every provider call and tool execution, in-process. There's
  no `/traces` endpoint yet — it exists for debugging and as a foundation
  for future cost dashboards, not for end-user consumption.
- **Evaluation** (`ai_platform/evaluation/`) — an offline harness you run in
  code or CI against a set of test cases (`EvalRunner`), not something an
  end user calls over HTTP.

## CLI Chat Client

A zero-dependency (stdlib-only) interactive terminal client ships alongside
the server and is installed by the same `pip install -e .`:

```bash
ai-platform-chat
```

```
Connected to http://localhost:8000 (conversation_id=...). Type 'exit' or Ctrl+C to quit.

You: My favorite color is teal.
Assistant: Nice choice! Teal is a great color...

You: What is my favorite color?
Assistant: Your favorite color is teal!

You: exit
Bye.
```

It talks to `POST /v1/chat` exactly like the `curl` examples above — it just
generates one `conversation_id` for the session so you don't have to pass
it yourself, and prints Gateway errors as a clean one-liner instead of raw
JSON. Useful flags:

```bash
ai-platform-chat --url http://localhost:8000 --api-key dev-local-key \
  --model claude-opus-4-8 --system "Reply concisely."
```

See [09-cli-client.md](engineer-tutorial/09-cli-client.md) for why it's
built as a standalone HTTP caller with zero imports from the rest of
`ai_platform/`.

## Tests

```bash
pytest
```

71 tests across all modules, run against fakes (`FakeModelProvider`,
`FakeRuntimeClient`, in-memory stores) — no real model calls required.

## Running with Docker

```bash
docker compose up --build
```

Builds the image from the `Dockerfile` (multi-stage: install into a
throwaway prefix, copy into a slim non-root runtime image) and runs the
Gateway on `localhost:8000`. `docker compose` picks up `AI_PLATFORM_*`
variables from the shell or a `.env` file automatically.

Without Compose:

```bash
docker build -t ai-platform .
docker run -p 8000:8000 -e AI_PLATFORM_ANTHROPIC_API_KEY=sk-... ai-platform
```

CI (`.github/workflows/ci.yml`) runs the full test suite on every push and
pull request to `main`.

## Status

All nine originally planned modules are implemented, tested, and documented.
A tenth, **Sandbox** (`ai_platform/sandbox/`), was added afterward to close a
gap module 04 deliberately deferred: tool execution had no timeout or
resource ceiling even though a tool call's arguments are chosen by the
model — untrusted input by construction. Further work is evolutionary — see
each tutorial's *Production Evolution* section for the concrete next steps (a
second `ModelProvider`, a Redis-backed `MemoryStore`/`Tracer` for
multi-replica deployments, an LLM-as-judge `Grader`, a container-based
`Sandbox`, publishing the image to a registry).
