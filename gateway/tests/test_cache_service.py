from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gateway.app.cache_service import SemanticCache
from gateway.app.vector_store import VectorStore


class FakeEmbeddingService:
    def encode_query(self, text: str) -> list[float]:
        return [1.0, 0.0] if "different" not in text else [0.0, 1.0]

    def encode_passage(self, text: str) -> list[float]:
        return self.encode_query(text)


@pytest.mark.asyncio
async def test_second_similar_question_hits_cache(tmp_path: Path) -> None:
    store = VectorStore(str(tmp_path / "chroma"))
    cache = SemanticCache(FakeEmbeddingService(), store, similarity_threshold=0.92)
    calls = 0

    async def generate_answer() -> str:
        nonlocal calls
        calls += 1
        return "generated answer"

    first = await cache.get_or_create("first question", generate_answer)
    second = await cache.get_or_create("first question", generate_answer)

    assert first.hit is False
    assert second.hit is True
    assert second.answer == "generated answer"
    assert calls == 1


@pytest.mark.asyncio
async def test_low_similarity_does_not_hit_cache(tmp_path: Path) -> None:
    store = VectorStore(str(tmp_path / "chroma"))
    cache = SemanticCache(FakeEmbeddingService(), store, similarity_threshold=0.92)
    calls = 0

    async def generate_answer() -> str:
        nonlocal calls
        calls += 1
        return f"answer {calls}"

    await cache.get_or_create("first question", generate_answer)
    result = await cache.get_or_create("different question", generate_answer)

    assert result.hit is False
    assert result.answer == "answer 2"
    assert calls == 2


@pytest.mark.asyncio
async def test_expired_entry_does_not_hit_cache(tmp_path: Path) -> None:
    store = VectorStore(str(tmp_path / "chroma"))
    current_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cache = SemanticCache(
        FakeEmbeddingService(),
        store,
        ttl_seconds=60,
        clock=lambda: current_time,
    )
    calls = 0

    async def generate_answer() -> str:
        nonlocal calls
        calls += 1
        return f"answer {calls}"

    await cache.get_or_create("first question", generate_answer)
    current_time = current_time + timedelta(seconds=61)
    result = await cache.get_or_create("first question", generate_answer)

    assert result.hit is False
    assert result.answer == "answer 2"
    assert calls == 2
