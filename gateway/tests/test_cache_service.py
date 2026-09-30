from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gateway.app.cache_service import GeneratedAnswer, SemanticCache
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

    async def generate_answer() -> GeneratedAnswer:
        nonlocal calls
        calls += 1
        return GeneratedAnswer("generated answer", {"total_tokens": 8})

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

    async def generate_answer() -> GeneratedAnswer:
        nonlocal calls
        calls += 1
        return GeneratedAnswer(f"answer {calls}", {"total_tokens": 8})

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

    async def generate_answer() -> GeneratedAnswer:
        nonlocal calls
        calls += 1
        return GeneratedAnswer(f"answer {calls}", {"total_tokens": 8})

    await cache.get_or_create("first question", generate_answer)
    current_time = current_time + timedelta(seconds=61)
    result = await cache.get_or_create("first question", generate_answer)

    assert result.hit is False
    assert result.answer == "answer 2"
    assert calls == 2


@pytest.mark.asyncio
async def test_semantic_mode_reuses_answer_for_similar_but_different_text(
    tmp_path: Path,
) -> None:
    """默认 semantic 模式下，向量相近即命中——这正是 exact 模式要规避的行为。

    这里两段文字不同但向量完全相同（假实现按关键词给向量），
    相当于提示词模板占了绝大部分篇幅、只有小段内容在变的批量请求。
    """
    store = VectorStore(str(tmp_path / "chroma"))
    cache = SemanticCache(FakeEmbeddingService(), store, similarity_threshold=0.92)
    calls = 0

    async def generate_answer() -> GeneratedAnswer:
        nonlocal calls
        calls += 1
        return GeneratedAnswer(f"answer {calls}", {"total_tokens": 8})

    await cache.get_or_create("请分析标题：第一条资讯", generate_answer)
    result = await cache.get_or_create("请分析标题：第二条资讯", generate_answer)

    assert result.hit is True
    assert result.answer == "answer 1"
    assert calls == 1


@pytest.mark.asyncio
async def test_exact_mode_does_not_reuse_answer_for_different_text(tmp_path: Path) -> None:
    """exact 模式下只有文本完全相同才复用。

    这是批量采集场景必须的行为：20 条不同的资讯各要各的摘要，
    绝不能因为模板雷同而共用第一条的答案。
    """
    store = VectorStore(str(tmp_path / "chroma"))
    cache = SemanticCache(FakeEmbeddingService(), store, similarity_threshold=0.92)
    calls = 0

    async def generate_answer() -> GeneratedAnswer:
        nonlocal calls
        calls += 1
        return GeneratedAnswer(f"answer {calls}", {"total_tokens": 8})

    first = await cache.get_or_create(
        "请分析标题：第一条资讯", generate_answer, cache_mode="exact"
    )
    second = await cache.get_or_create(
        "请分析标题：第二条资讯", generate_answer, cache_mode="exact"
    )
    repeat = await cache.get_or_create(
        "请分析标题：第一条资讯", generate_answer, cache_mode="exact"
    )

    assert first.hit is False
    assert second.hit is False, "文本不同就不该复用"
    assert second.answer == "answer 2", "必须拿到自己的答案，而不是第一条的"
    assert repeat.hit is True, "文本完全相同应当复用"
    assert repeat.answer == "answer 1"
    assert calls == 2
