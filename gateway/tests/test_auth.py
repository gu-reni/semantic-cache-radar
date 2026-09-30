"""网关鉴权行为的测试。

网关持有上游大模型的 API Key，一旦对外可达又不校验身份，
就等于把 Key 借给任何扫到端口的人。这里把四种情况都钉住：
未设令牌（开发模式）、设了令牌但没带、带错、带对。
"""
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gateway.app.cache_service import CacheResult, CacheStats, GeneratedAnswer
from gateway.app.config import get_settings
from gateway.app.main import app, get_llm_client, get_semantic_cache

TOKEN = "test-token-0123456789abcdef"
CHAT_BODY = {"messages": [{"role": "user", "content": "hello"}]}


class FakeLLMClient:
    def __init__(self) -> None:
        self.called = False

    async def chat_completion(self, request: dict[str, Any]) -> dict[str, Any]:
        self.called = True
        return {
            "choices": [{"message": {"role": "assistant", "content": "upstream answer"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


class FakeSemanticCache:
    def __init__(self) -> None:
        self.cleared = False

    async def get_or_create(
        self,
        question: str,
        generate_answer: Any,
        metadata: Any,
        cache_mode: str = "semantic",
    ) -> CacheResult:
        generated = await generate_answer()
        assert isinstance(generated, GeneratedAnswer)
        return CacheResult(answer=generated.answer, hit=False, cache_id="id", usage=generated.usage)

    def stats(self) -> CacheStats:
        return CacheStats(0, 0, 0, 0, 0, 0, 0, 0)

    def clear(self) -> None:
        self.cleared = True


@pytest.fixture
def secured(monkeypatch: pytest.MonkeyPatch):
    """把网关配置成「已设共享令牌」。"""
    monkeypatch.setenv("GATEWAY_AUTH_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def open_gateway(monkeypatch: pytest.MonkeyPatch):
    """把网关配置成「未设共享令牌」，即本地开发模式。"""
    monkeypatch.delenv("GATEWAY_AUTH_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fakes():
    client = FakeLLMClient()
    cache = FakeSemanticCache()
    app.dependency_overrides[get_llm_client] = lambda: client
    app.dependency_overrides[get_semantic_cache] = lambda: cache
    yield client, cache
    app.dependency_overrides.clear()


def test_chat_rejected_without_token_when_configured(secured, fakes) -> None:
    response = TestClient(app).post("/v1/chat/completions", json=CHAT_BODY)

    assert response.status_code == 401
    assert fakes[0].called is False


def test_chat_rejected_with_wrong_token(secured, fakes) -> None:
    response = TestClient(app).post(
        "/v1/chat/completions",
        json=CHAT_BODY,
        headers={"X-Gateway-Token": "not-the-right-token"},
    )

    assert response.status_code == 401
    assert fakes[0].called is False


def test_chat_accepted_with_x_gateway_token(secured, fakes) -> None:
    response = TestClient(app).post(
        "/v1/chat/completions",
        json=CHAT_BODY,
        headers={"X-Gateway-Token": TOKEN},
    )

    assert response.status_code == 200
    assert fakes[0].called is True


def test_chat_accepted_with_bearer_authorization(secured, fakes) -> None:
    response = TestClient(app).post(
        "/v1/chat/completions",
        json=CHAT_BODY,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 200
    assert fakes[0].called is True


def test_clear_cache_rejected_without_token(secured, fakes) -> None:
    response = TestClient(app).delete("/cache")

    assert response.status_code == 401
    assert fakes[1].cleared is False


def test_clear_cache_accepted_with_token(secured, fakes) -> None:
    response = TestClient(app).delete("/cache", headers={"X-Gateway-Token": TOKEN})

    assert response.status_code == 200
    assert fakes[1].cleared is True


def test_health_stays_open_even_when_secured(secured, fakes) -> None:
    """探活接口要能被监控无凭证访问，否则编排层的健康检查会失败。"""
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cache_stats_stays_open_even_when_secured(secured, fakes) -> None:
    response = TestClient(app).get("/cache/stats")

    assert response.status_code == 200
    assert response.json()["requests"] == 0


def test_chat_open_when_token_not_configured(open_gateway, fakes) -> None:
    """未设令牌时不校验，保证本地开发不用先造一个令牌。"""
    response = TestClient(app).post("/v1/chat/completions", json=CHAT_BODY)

    assert response.status_code == 200
    assert fakes[0].called is True
