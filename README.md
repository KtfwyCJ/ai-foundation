# ai-foundation

A Production-Inspired AI Platform Framework

## Modules

- `ai_platform/common/` — shared config, errors, schemas, interfaces
- `ai_platform/api/` — Gateway: auth, rate limiting, routing
- `ai_platform/providers/` — Model provider abstraction (Anthropic)
- `ai_platform/runtime/` — Agent loop / orchestrator: provider + tools + memory + tracing
- `ai_platform/tools/` — Tool Registry: pluggable, model-callable tools
- `ai_platform/memory/` — Conversation history persistence across requests
- `ai_platform/tracing/` — Span-based observability over provider calls and tool executions
- `ai_platform/evaluation/` — Offline eval harness: run test cases through any RuntimeClient and grade the responses

See `engineer-tutorial/` for a deep-dive tutorial on each module.

## Running the Gateway

```bash
pip install -e ".[dev]"
uvicorn ai_platform.api.app:app --reload
```

Try it:

```bash
curl -X POST localhost:8000/v1/chat \
  -H "Authorization: Bearer dev-local-key" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "hello"}]}'
```

## Tests

```bash
pytest
```

## Running with Docker

```bash
docker compose up --build
```

This builds the image from the `Dockerfile` (multi-stage: install into a
throwaway prefix, copy into a slim non-root runtime image) and runs the
Gateway on `localhost:8000`. Configuration is environment-driven (see
`ai_platform/common/config.py`); set values in a `.env` file or export them
before running — `docker compose` picks up `AI_PLATFORM_ANTHROPIC_API_KEY`,
`AI_PLATFORM_API_KEYS`, etc. from the shell/`.env` automatically.

Without Compose:

```bash
docker build -t ai-platform .
docker run -p 8000:8000 -e AI_PLATFORM_ANTHROPIC_API_KEY=sk-... ai-platform
```

CI (`.github/workflows/ci.yml`) runs the full test suite on every push and
pull request to `main`.
