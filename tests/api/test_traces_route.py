from ai_platform.api.dependencies import get_tracer
from ai_platform.tracing.types import Span


async def test_get_trace_returns_recorded_spans(client):
    tracer = get_tracer()
    await tracer.record(
        Span(trace_id="trace-1", name="provider.complete", duration_ms=12.5, attributes={"model": "claude-sonnet-5"})
    )

    response = client.get("/v1/traces/trace-1", headers={"Authorization": "Bearer dev-local-key"})

    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == "trace-1"
    assert len(body["spans"]) == 1
    assert body["spans"][0]["name"] == "provider.complete"
    assert body["spans"][0]["attributes"]["model"] == "claude-sonnet-5"


def test_get_trace_for_unknown_id_returns_404(client):
    response = client.get("/v1/traces/does-not-exist", headers={"Authorization": "Bearer dev-local-key"})

    assert response.status_code == 404
    assert response.json()["error"] == "TraceNotFoundError"


def test_get_trace_without_auth_header_is_rejected(client):
    response = client.get("/v1/traces/trace-1")

    assert response.status_code == 401
