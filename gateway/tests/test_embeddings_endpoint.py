"""算向量接口的测试。

这个接口是给雷达做「两条标题是不是在讲同一件事」用的，它有两个容易出错、
而且出错时不会报错的地方，所以各钉一条：

  1. 前缀选错（e5 要求 query:/passage:）只会让相似度整体偏低，
     表现成「明明是一回事却判成不相似」，不测试就发现不了；
  2. 每个 EmbeddingService 都是一份独立的 ONNX 会话（模型 113MB），
     同一进程里再建一份等于把内存翻倍 —— 所以必须共用同一份实例。
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from gateway.app.cache_service import SemanticCache
from gateway.app.config import get_settings
from gateway.app.main import app, get_semantic_cache

TOKEN = "test-token-0123456789abcdef"


class FakeEmbeddingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def _record(self, kind: str, text: str) -> list[float]:
        self.calls.append((kind, text))
        return [float(len(self.calls)), 0.0, 1.0]

    def encode_symmetric(self, text: str) -> list[float]:
        return self._record("symmetric", text)

    def encode_query(self, text: str) -> list[float]:
        return self._record("query", text)

    def encode_passage(self, text: str) -> list[float]:
        return self._record("passage", text)


class FakeSemanticCache:
    def __init__(self) -> None:
        self._embedding_service = FakeEmbeddingService()

    @property
    def embedding_service(self) -> FakeEmbeddingService:
        return self._embedding_service


@pytest.fixture
def secured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GATEWAY_AUTH_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def cache() -> Any:
    fake = FakeSemanticCache()
    app.dependency_overrides[get_semantic_cache] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def test_embeddings_rejected_without_token(secured, cache) -> None:
    """它是算力接口：敞开等于把 CPU 借给任何扫到端口的人。"""
    response = TestClient(app).post("/v1/embeddings", json={"input": ["标题"]})

    assert response.status_code == 401
    assert cache.embedding_service.calls == []


def test_embeddings_return_one_vector_per_input_in_order(secured, cache) -> None:
    response = TestClient(app).post(
        "/v1/embeddings",
        json={"input": ["第一条", "第二条", "第三条"]},
        headers={"X-Gateway-Token": TOKEN},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["index"] for item in data] == [0, 1, 2]
    assert all(len(item["embedding"]) == 3 for item in data)
    assert [text for _, text in cache.embedding_service.calls] == ["第一条", "第二条", "第三条"]


@pytest.mark.parametrize(
    ("input_type", "expected_kind"),
    [("symmetric", "symmetric"), ("query", "query"), ("passage", "passage")],
)
def test_embeddings_use_the_requested_encoder(
    secured, cache, input_type: str, expected_kind: str
) -> None:
    """前缀由 input_type 决定，默认走 symmetric（标题对标题这种同质比较）。"""
    client = TestClient(app)
    client.post(
        "/v1/embeddings",
        json={"input": ["x"], "input_type": input_type},
        headers={"X-Gateway-Token": TOKEN},
    )
    assert cache.embedding_service.calls[0][0] == expected_kind


def test_embeddings_default_to_symmetric(secured, cache) -> None:
    TestClient(app).post(
        "/v1/embeddings",
        json={"input": ["x"]},
        headers={"X-Gateway-Token": TOKEN},
    )
    assert cache.embedding_service.calls[0][0] == "symmetric"


def test_embeddings_reject_empty_input(secured, cache) -> None:
    response = TestClient(app).post(
        "/v1/embeddings",
        json={"input": []},
        headers={"X-Gateway-Token": TOKEN},
    )

    assert response.status_code == 422
    assert cache.embedding_service.calls == []


def test_embeddings_reject_oversized_batch(secured, cache) -> None:
    """限制单次批量，避免一次请求就把 CPU 占满。"""
    response = TestClient(app).post(
        "/v1/embeddings",
        json={"input": ["x"] * 65},
        headers={"X-Gateway-Token": TOKEN},
    )

    assert response.status_code == 422
    assert cache.embedding_service.calls == []


def test_embedding_service_is_shared_not_recreated() -> None:
    """同一个 SemanticCache 必须始终返回同一个 embedding service。

    每份实例都是一次 ONNX 会话（模型 113MB）。要是这里每次都新建，
    内存会随调用次数翻倍，而容器内存是有上限的。
    """
    service = FakeEmbeddingService()
    cache = SemanticCache(
        embedding_service=service,  # type: ignore[arg-type]
        vector_store=object(),  # type: ignore[arg-type]
        similarity_threshold=0.92,
        ttl_seconds=0,
    )

    assert cache.embedding_service is service
    assert cache.embedding_service is cache.embedding_service
