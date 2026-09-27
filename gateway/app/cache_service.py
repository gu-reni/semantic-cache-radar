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


class SemanticCache:
    """Coordinate embedding, vector search, TTL checks, and cache writes."""

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

    async def get_or_create(
        self,
        question: str,
        generate_answer: Callable[[], Awaitable[str]],
        metadata: dict[str, Any] | None = None,
    ) -> CacheResult:
        query_embedding = self._embedding_service.encode_query(question)
        matches = self._vector_store.search(query_embedding, limit=5)
        now = self._clock()

        for match in matches:
            if self._is_valid_match(match, now):
                return CacheResult(
                    answer=match.answer,
                    hit=True,
                    similarity=match.similarity,
                    cache_id=match.cache_id,
                )

        answer = await generate_answer()
        expires_at = now + timedelta(seconds=self._ttl_seconds)
        entry_metadata = {
            **(metadata or {}),
            "expires_at": expires_at.isoformat(),
        }
        passage_embedding = self._embedding_service.encode_passage(question)
        cache_id = self._vector_store.add(
            question=question,
            answer=answer,
            embedding=passage_embedding,
            metadata=entry_metadata,
        )
        return CacheResult(answer=answer, hit=False, cache_id=cache_id)

    def _is_valid_match(self, match: VectorMatch, now: datetime) -> bool:
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
