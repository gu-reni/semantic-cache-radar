"""统计按缓存模式分记 + 持久化的测试。

钉住三件事：
1. exact 命中（文本完全一致）与 semantic 命中（文本不同但相似度过阈值）分开计数；
2. 计数器落盘后，新实例从磁盘恢复、重启不丢；
3. /cache/stats 老字段不消失，且新增 exact_hits / semantic_hits。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.app.cache_service import CacheStats, GeneratedAnswer, SemanticCache
from gateway.app.main import app, get_semantic_cache
from gateway.app.vector_store import VectorStore


class SameVectorEmbeddingService:
    """所有文本都映射到同一个向量，用来逼出「文本不同但相似度 1.0」的语义命中。"""

    def encode_query(self, text: str) -> list[float]:
        return [1.0, 0.0]

    def encode_passage(self, text: str) -> list[float]:
        return [1.0, 0.0]


@pytest.mark.asyncio
async def test_exact_and_semantic_hits_are_counted_separately(tmp_path: Path) -> None:
    store = VectorStore(str(tmp_path / "chroma"))
    cache = SemanticCache(SameVectorEmbeddingService(), store, similarity_threshold=0.92)

    async def generate_answer() -> GeneratedAnswer:
        return GeneratedAnswer("answer", {"total_tokens": 8})

    # 第一次：未命中，存入。
    await cache.get_or_create("alpha", generate_answer)
    # 第二次：文本完全一致 -> exact 命中。
    await cache.get_or_create("alpha", generate_answer)
    # 第三次：文本不同但向量相同 -> semantic 命中。
    await cache.get_or_create("beta", generate_answer)

    stats = cache.stats()
    assert stats.exact_hits == 1
    assert stats.semantic_hits == 1
    assert stats.misses == 1
    assert stats.hits == 2
    assert stats.requests == 3


@pytest.mark.asyncio
async def test_stats_survive_restart_via_disk(tmp_path: Path) -> None:
    stats_path = str(tmp_path / "stats.json")
    store = VectorStore(str(tmp_path / "chroma"))
    cache = SemanticCache(
        SameVectorEmbeddingService(),
        store,
        similarity_threshold=0.92,
        stats_path=stats_path,
    )

    async def generate_answer() -> GeneratedAnswer:
        return GeneratedAnswer("answer", {"total_tokens": 8})

    await cache.get_or_create("alpha", generate_answer)
    await cache.get_or_create("alpha", generate_answer)

    # 模拟重启：用同一个 stats_path 建一个新实例（新 vector store，只验证计数器）。
    restarted = SemanticCache(
        SameVectorEmbeddingService(),
        VectorStore(str(tmp_path / "chroma2")),
        similarity_threshold=0.92,
        stats_path=stats_path,
    )
    stats = restarted.stats()
    assert stats.exact_hits == 1
    assert stats.misses == 1


def test_corrupted_stats_file_is_ignored(tmp_path: Path) -> None:
    stats_path = tmp_path / "stats.json"
    stats_path.write_text("{ not valid json", encoding="utf-8")

    cache = SemanticCache(
        SameVectorEmbeddingService(),
        VectorStore(str(tmp_path / "chroma")),
        stats_path=str(stats_path),
    )
    assert cache.stats().requests == 0


class FakeSemanticCache:
    def stats(self) -> CacheStats:
        return CacheStats(
            requests=3,
            hits=2,
            misses=1,
            stored_entries=4,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            saved_tokens=40,
            exact_hits=1,
            semantic_hits=1,
        )


def test_cache_stats_endpoint_exposes_new_fields_without_dropping_old() -> None:
    """向后兼容：老字段一个不少，新字段也要有。"""
    app.dependency_overrides[get_semantic_cache] = lambda: FakeSemanticCache()
    try:
        response = TestClient(app).get("/cache/stats")
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    for old_field in (
        "requests",
        "hits",
        "misses",
        "hit_rate",
        "stored_entries",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "saved_tokens",
    ):
        assert old_field in body, f"老字段 {old_field} 不能消失"
    assert body["exact_hits"] == 1
    assert body["semantic_hits"] == 1
