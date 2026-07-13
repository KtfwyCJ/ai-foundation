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

## Tests

```bash
pytest
```

67+ tests across all modules, run against fakes (`FakeModelProvider`,
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

All planned modules are implemented, tested, and documented. Further work is
evolutionary — see each tutorial's *Production Evolution* section for the
concrete next steps (a second `ModelProvider`, a Redis-backed `MemoryStore`/
`Tracer` for multi-replica deployments, an LLM-as-judge `Grader`, publishing
the image to a registry).
