def test_chat_without_auth_header_is_rejected(client):
    response = client.post("/v1/chat", json={"messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 401
    assert response.json()["error"] == "AuthenticationError"


def test_chat_with_invalid_api_key_is_rejected(client):
    response = client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer not-a-real-key"},
    )

    assert response.status_code == 401


def test_chat_with_valid_api_key_is_accepted(client):
    response = client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer dev-local-key"},
    )

    assert response.status_code == 200
