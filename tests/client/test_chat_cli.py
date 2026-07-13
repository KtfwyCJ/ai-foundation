import io
import json
import urllib.error

import pytest

from ai_platform.client.chat_cli import GatewayError, send_message


class _FakeHTTPResponse:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        return None


def test_send_message_posts_expected_request_shape(monkeypatch):
    captured = {}

    def fake_urlopen(request):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data)
        return _FakeHTTPResponse({"message": {"role": "assistant", "content": "hi"}, "model": "claude-sonnet-5"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = send_message(
        base_url="http://localhost:8000",
        api_key="dev-local-key",
        model="claude-sonnet-5",
        conversation_id="conv-1",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert captured["url"] == "http://localhost:8000/v1/chat"
    assert captured["method"] == "POST"
    assert captured["headers"]["Authorization"] == "Bearer dev-local-key"
    assert captured["body"] == {
        "messages": [{"role": "user", "content": "hello"}],
        "model": "claude-sonnet-5",
        "conversation_id": "conv-1",
    }
    assert result == {"message": {"role": "assistant", "content": "hi"}, "model": "claude-sonnet-5"}


def test_send_message_raises_gateway_error_with_platform_detail(monkeypatch):
    error_body = json.dumps({"error": "AuthenticationError", "detail": "Invalid API key"}).encode()

    def fake_urlopen(request):
        raise urllib.error.HTTPError(
            url=request.full_url, code=401, msg="Unauthorized", hdrs=None, fp=io.BytesIO(error_body)
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(GatewayError, match="AuthenticationError: Invalid API key"):
        send_message(
            base_url="http://localhost:8000",
            api_key="bad-key",
            model="claude-sonnet-5",
            conversation_id="conv-1",
            messages=[{"role": "user", "content": "hi"}],
        )
