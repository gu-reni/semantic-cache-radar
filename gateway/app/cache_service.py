from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from gateway.app.embeddings import EmbeddingService
from gateway.app.vector_store import VectorMatch, VectorStore


@dataclass(frozen=True)
class CacheResult:
    answer: str
    hit: bool
    similarity: float | None = None
    cache_id: str | None = None
    usage: dict[str, int] | None = None
    saved_tokens: int = 0


@dataclass(frozen=True)
class GeneratedAnswer:
    answer: str
    usage: dict[str, int]


@dataclass(frozen=True)
class CacheStats:
    requests: int
    hits: int
    misses: int
    stored_entries: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    saved_tokens: int

    @property
    def hit_rate(self) -> float:
        return self.hits / self.requests if self.requests else 0.0


class SemanticCache:
    """Coordinate embedding, vector search, TTL checks, and cache writes."""

    @property
    def embedding_service(self) -> EmbeddingService:
        """暴露给同进程的其它接口复用。

        为什么不让调用方各自新建一个 EmbeddingService：每个实例都是一份
        独立的 ONNX 会话（模型 113MB，常驻内存数百 MB）。同一进程里再建一份
        等于把内存翻倍，而容器是有上限的。共用同一份实例既省内存，
        也保证金进程对同一段文本算出的是同一个向量。
        """
        return self._embedding_service

    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
        similarity_threshold: float = 0.92,
        ttl_seconds: int = 86400,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._similarity_threshold = similarity_threshold
        self._ttl_seconds = ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._requests = 0
        self._hits = 0
        self._misses = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._saved_tokens = 0

    async def get_or_create(
        self,
        question: str,
        generate_answer: Callable[[], Awaitable[GeneratedAnswer]],
        metadata: dict[str, Any] | None = None,
        cache_mode: str = "semantic",
    ) -> CacheResult:
        self._requests += 1
        query_embedding = self._embedding_service.encode_query(question)
        matches = self._vector_store.search(query_embedding, limit=5)
        now = self._clock()

        for match in matches:
            if self._is_valid_match(match, now, question, cache_mode):
                self._hits += 1
                saved_tokens = int(match.metadata.get("total_tokens", 0))
                self._saved_tokens += saved_tokens
                return CacheResult(
                    answer=match.answer,
                    hit=True,
                    similarity=match.similarity,
                    cache_id=match.cache_id,
                    usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    saved_tokens=saved_tokens,
                )

        self._misses += 1
        generated = await generate_answer()
        usage = {
            "prompt_tokens": int(generated.usage.get("prompt_tokens", 0)),
            "completion_tokens": int(generated.usage.get("completion_tokens", 0)),
            "total_tokens": int(generated.usage.get("total_tokens", 0)),
        }
        self._prompt_tokens += usage["prompt_tokens"]
        self._completion_tokens += usage["completion_tokens"]
        self._total_tokens += usage["total_tokens"]
        expires_at = now + timedelta(seconds=self._ttl_seconds)
        entry_metadata = {
            **(metadata or {}),
            "expires_at": expires_at.isoformat(),
            **usage,
        }
        passage_embedding = self._embedding_service.encode_passage(question)
        cache_id = self._vector_store.add(
            question=question,
            answer=generated.answer,
            embedding=passage_embedding,
            metadata=entry_metadata,
        )
        return CacheResult(answer=generated.answer, hit=False, cache_id=cache_id, usage=usage)

    def stats(self) -> CacheStats:
        return CacheStats(
            requests=self._requests,
            hits=self._hits,
            misses=self._misses,
            stored_entries=self._vector_store.count(),
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._total_tokens,
            saved_tokens=self._saved_tokens,
        )

    def clear(self) -> None:
        self._vector_store.clear()
        self._requests = 0
        self._hits = 0
        self._misses = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._saved_tokens = 0

    def _is_valid_match(
        self,
        match: VectorMatch,
        now: datetime,
        question: str,
        cache_mode: str,
    ) -> bool:
        """判断候选条目能否复用。

        cache_mode 为什么存在：
          语义缓存按向量相似度复用答案，前提是「问题不同则语义不同」。
          但雷达的提示词里模板占了绝大部分篇幅，只有标题在变，
          整批提示词的余弦相似度都会越过阈值，结果 20 条资讯共用同一个摘要。
          这类场景只能按文本完全一致来复用，于是有了 exact 模式。
        """
        if cache_mode == "exact" and match.question != question:
            return False

        if match.similarity < self._similarity_threshold:
            return False

        expires_at_value = match.metadata.get("expires_at")
        if not isinstance(expires_at_value, str):
            return False

        try:
            expires_at = datetime.fromisoformat(expires_at_value)
        except ValueError:
            return False

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > now
