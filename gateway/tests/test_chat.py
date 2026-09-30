from typing import Any

from fastapi.testclient import TestClient

from gateway.app.cache_service import CacheResult, GeneratedAnswer
from gateway.app.main import app, get_llm_client, get_semantic_cache


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


class FakeSemanticCache:
    def __init__(self, hit: bool = False) -> None:
        self.hit = hit
        self.cache_mode: str | None = None

    async def get_or_create(
        self,
        question: str,
        generate_answer: Any,
        metadata: Any,
        cache_mode: str = "semantic",
    ) -> CacheResult:
        self.cache_mode = cache_mode
        if self.hit:
            return CacheResult(
                answer="cached answer",
                hit=True,
                similarity=0.97,
                cache_id="cache-test",
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
        generated = await generate_answer()
        assert isinstance(generated, GeneratedAnswer)
        return CacheResult(answer=generated.answer, hit=False, cache_id="cache-test", usage=generated.usage)


def test_chat_completion_forwards_request() -> None:
    fake_client = FakeLLMClient()
    fake_cache = FakeSemanticCache()
    app.dependency_overrides[get_llm_client] = lambda: fake_client
    app.dependency_overrides[get_semantic_cache] = lambda: fake_cache

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
    assert response.headers["X-Cache"] == "MISS"
    assert response.json()["choices"][0]["message"]["content"] == "test answer"
    assert response.json()["usage"] == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
    assert fake_client.received == {
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_chat_completion_returns_cached_answer_without_upstream_call() -> None:
    fake_client = FakeLLMClient()
    fake_cache = FakeSemanticCache(hit=True)
    app.dependency_overrides[get_llm_client] = lambda: fake_client
    app.dependency_overrides[get_semantic_cache] = lambda: fake_cache

    try:
        response = TestClient(app).post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["X-Cache"] == "HIT"
    assert response.headers["X-Cache-Similarity"] == "0.9700"
    assert response.json()["choices"][0]["message"]["content"] == "cached answer"
    assert fake_client.received is None
