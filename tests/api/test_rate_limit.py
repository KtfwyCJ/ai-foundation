import os


def test_requests_beyond_limit_are_rejected(client, monkeypatch):
    monkeypatch.setenv("AI_PLATFORM_RATE_LIMIT_REQUESTS", "2")
    headers = {"Authorization": "Bearer dev-local-key"}
    payload = {"messages": [{"role": "user", "content": "hi"}]}

    assert client.post("/v1/chat", json=payload, headers=headers).status_code == 200
    assert client.post("/v1/chat", json=payload, headers=headers).status_code == 200

    response = client.post("/v1/chat", json=payload, headers=headers)

    assert response.status_code == 429
    assert response.json()["error"] == "RateLimitExceededError"
