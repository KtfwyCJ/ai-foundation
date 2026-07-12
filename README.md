# ai-foundation

A Production-Inspired AI Platform Framework

## Modules

- `ai_platform/common/` — shared config, errors, schemas, interfaces
- `ai_platform/api/` — Gateway: auth, rate limiting, routing (implemented)
- `ai_platform/runtime/` — Agent loop / orchestrator (stub only, pending)

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
