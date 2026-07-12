def test_chat_echoes_last_user_message(client):
    response = client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "hello platform"}]},
        headers={"Authorization": "Bearer dev-local-key"},
    )

    body = response.json()
    assert body["message"]["content"] == "echo: hello platform"
    assert body["message"]["role"] == "assistant"


def test_chat_rejects_empty_messages(client):
    response = client.post(
        "/v1/chat",
        json={"messages": []},
        headers={"Authorization": "Bearer dev-local-key"},
    )

    assert response.status_code == 422
