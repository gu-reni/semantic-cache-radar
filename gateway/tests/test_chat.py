from typing import Any

from fastapi.testclient import TestClient

from gateway.app.config import Settings
from gateway.app.llm_client import LLMClient
from gateway.app.main import app, get_llm_client


class FakeCompletion:
    def model_dump(self) -> dict[str, Any]:
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "test answer"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }


class FakeLLMClient:
    def __init__(self) -> None:
        self.received: dict[str, Any] | None = None

    async def chat_completion(self, request: dict[str, Any]) -> dict[str, Any]:
        self.received = request
        return FakeCompletion().model_dump()


def test_chat_completion_forwards_request() -> None:
    fake_client = FakeLLMClient()
    app.dependency_overrides[get_llm_client] = lambda: fake_client

    try:
        response = TestClient(app).post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "test answer"
    assert fake_client.received == {
        "messages": [{"role": "user", "content": "hello"}],
    }
