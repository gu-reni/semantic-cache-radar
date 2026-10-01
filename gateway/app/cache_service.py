import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from gateway.app.embeddings import EmbeddingService
from gateway.app.vector_store import VectorMatch, VectorStore


def _coerce_int(value: Any) -> int:
    """把从统计文件里读到的值安全地转成 int；读不到或非法就当作 0。

    统计文件可能被人手改过、或旧版本写过不同字段，这里绝不能因为一个坏值
    就让网关启动失败 —— 它只是观测数据。
    """
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
    exact_hits: int = 0
    semantic_hits: int = 0

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
        stats_path: str | None = None,
    ) -> None:
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._similarity_threshold = similarity_threshold
        self._ttl_seconds = ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # stats_path 为 None 时统计只留在内存（测试与本地开发用）；
        # 生产由 main.get_semantic_cache 注入一个磁盘路径，重启不丢。
        self._stats_path = stats_path
        self._exact_hits = 0
        self._semantic_hits = 0
        self._misses = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._saved_tokens = 0
        self._load_stats()

    async def get_or_create(
        self,
        question: str,
        generate_answer: Callable[[], Awaitable[GeneratedAnswer]],
        metadata: dict[str, Any] | None = None,
        cache_mode: str = "semantic",
    ) -> CacheResult:
        query_embedding = self._embedding_service.encode_query(question)
        matches = self._vector_store.search(query_embedding, limit=5)
        now = self._clock()

        for match in matches:
            if self._is_valid_match(match, now, question, cache_mode):
                # 按「命中是否因为文本完全一致」区分两种命中：
                #   文本完全一致 -> exact 命中；
                #   文本不同但相似度过阈值 -> semantic 命中（真正的语义复用）。
                # 这样 stats 能直接回答「语义缓存到底有没有真的被用上」，
                # 而不是靠读代码去推理 cache_mode 分支。
                if match.question == question:
                    self._exact_hits += 1
                else:
                    self._semantic_hits += 1
                saved_tokens = int(match.metadata.get("total_tokens", 0))
                self._saved_tokens += saved_tokens
                self._save_stats()
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
        self._save_stats()
        return CacheResult(answer=generated.answer, hit=False, cache_id=cache_id, usage=usage)

    def stats(self) -> CacheStats:
        exact_hits = self._exact_hits
        semantic_hits = self._semantic_hits
        misses = self._misses
        return CacheStats(
            requests=exact_hits + semantic_hits + misses,
            hits=exact_hits + semantic_hits,
            misses=misses,
            stored_entries=self._vector_store.count(),
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._total_tokens,
            saved_tokens=self._saved_tokens,
            exact_hits=exact_hits,
            semantic_hits=semantic_hits,
        )

    def clear(self) -> None:
        self._vector_store.clear()
        self._exact_hits = 0
        self._semantic_hits = 0
        self._misses = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._saved_tokens = 0
        self._save_stats()

    def _save_stats(self) -> None:
        """把计数器原子地写到磁盘，重启后不丢。

        用「写临时文件再 rename」保证任何时刻读到的要么是完整旧值、要么是完整新值，
        不会读到写了一半的 JSON。stats_path 为 None（测试/本地开发）时直接跳过。
        """
        if not self._stats_path:
            return
        path = Path(self._stats_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "exact_hits": self._exact_hits,
                "semantic_hits": self._semantic_hits,
                "misses": self._misses,
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "total_tokens": self._total_tokens,
                "saved_tokens": self._saved_tokens,
            },
            ensure_ascii=False,
        )
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)

    def _load_stats(self) -> None:
        """启动时从磁盘恢复计数器；文件缺失或损坏都当作没有，从零开始。"""
        if not self._stats_path:
            return
        path = Path(self._stats_path)
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 统计文件损坏时从零开始，而不是让网关起不来 —— 它只是观测数据。
            return
        self._exact_hits = _coerce_int(data.get("exact_hits"))
        self._semantic_hits = _coerce_int(data.get("semantic_hits"))
        self._misses = _coerce_int(data.get("misses"))
        self._prompt_tokens = _coerce_int(data.get("prompt_tokens"))
        self._completion_tokens = _coerce_int(data.get("completion_tokens"))
        self._total_tokens = _coerce_int(data.get("total_tokens"))
        self._saved_tokens = _coerce_int(data.get("saved_tokens"))

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
