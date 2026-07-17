# Trace Viewer — Design

*Date: 2026-07-17*

## Problem

Tracing (`ai_platform/tracing/`) records a `Span` for every provider call and tool
execution `RuntimeEngine` makes, keyed under a `trace_id`. `InMemoryTracer.record()`
only appends to an in-process dict — nothing prints or logs anywhere, and no API
route exposes `get_trace()`. Calling `/v1/chat` produces spans, but there is
currently no way to see them: not in the console, not over HTTP, and not even a
way to learn the `trace_id` a given request was recorded under (`ChatResponse`
doesn't return it).

## Goal

After calling `/v1/chat`, be able to look up exactly what happened on that
request — which provider/tool calls ran, how long each took, token usage, and
any errors — via a simple authenticated JSON endpoint. Keep it minimal: no new
storage backend, no UI, just a read path onto data that's already being
recorded.

## Changes

1. **`ai_platform/common/schemas.py`** — add `trace_id: str` to `ChatResponse`.

2. **`ai_platform/runtime/engine.py`** — `handle_chat` includes the `trace_id`
   it already computes (`conversation_id` or a generated uuid) in the
   `ChatResponse` it returns, instead of discarding it after the request
   finishes.

3. **`ai_platform/tracing/interfaces.py` / `in_memory.py`** — no change.
   `Tracer.get_trace(trace_id) -> list[Span]` already exists on `InMemoryTracer`
   and is sufficient for this read path.

4. **New route — `ai_platform/api/routes/traces.py`**:
   ```
   GET /v1/traces/{trace_id}
   auth:  requires Authorization: Bearer <key> (verify_api_key), no rate limiting
   200:   {"trace_id": "...", "spans": [Span, ...]}
          Span: {name, duration_ms, attributes, error}
   404:   no spans recorded for that trace_id
   ```
   Response model: a new `TraceResponse` Pydantic model in
   `ai_platform/common/schemas.py` (`trace_id: str`, `spans: list[Span]`).

5. **`ai_platform/api/dependencies.py`** — no change. The route depends on the
   existing `get_tracer()` directly (`Depends(get_tracer)`), the same way
   `chat.py` depends on `get_runtime_client` directly — no new wiring needed.

6. **`ai_platform/api/app.py`** — register the new `traces` router.

## Auth

Same `verify_api_key` dependency as `/v1/chat`. Traces can reveal model names,
token counts, and tool usage per conversation, so this is gated like the rest
of the real API rather than left open like `/health`. Rate limiting is not
applied — read-only lookups of a caller's own prior activity aren't the
resource rate limiting protects.

## Error handling

`GET /v1/traces/{trace_id}` for an id with no recorded spans returns HTTP 404
(`{"error": "TraceNotFoundError", "detail": "..."}`), following the existing
`PlatformError` → status-code mapping pattern in `ai_platform/api/errors.py`.
This requires adding `TraceNotFoundError` to `ai_platform/common/errors.py` and
a `404` entry in `errors.py`'s `_STATUS_CODES` map.

## Testing

New `tests/api/test_traces_route.py`:
- Call `/v1/chat`, take the returned `trace_id`, `GET /v1/traces/{trace_id}`,
  assert at least one `provider.complete` span is present with expected
  attributes.
- `GET /v1/traces/{unknown_id}` → 404.
- `GET /v1/traces/{trace_id}` without an `Authorization` header → 401.

## Non-goals (deferred)

- Listing all known trace_ids (`GET /v1/traces`) — no consumer needs it yet;
  the caller already has the id from `ChatResponse`.
- Any real-time console/log output of spans as they're recorded — this design
  is a pull-based lookup, not a tail-able log stream.
- Any UI beyond raw JSON.
- Pagination, filtering, or retention policy on `InMemoryTracer` — unchanged
  from the existing module's documented v0.1 limitations.
