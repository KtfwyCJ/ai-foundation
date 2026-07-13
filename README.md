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
