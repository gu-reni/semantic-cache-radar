from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import chromadb


@dataclass(frozen=True)
class VectorMatch:
    cache_id: str
    question: str
    answer: str
    similarity: float
    metadata: dict[str, Any]


class VectorStore:
    """Persistent ChromaDB storage for semantic cache entries."""

    def __init__(self, persist_directory: str, collection_name: str = "semantic_cache") -> None:
        Path(persist_directory).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_directory)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(
        self,
        question: str,
        answer: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        cache_id = str(uuid4())
        entry_metadata = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
        self._collection.add(
            ids=[cache_id],
            embeddings=[embedding],
            documents=[question],
            metadatas=[{**entry_metadata, "answer": answer}],
        )
        return cache_id

    def search(self, embedding: list[float], limit: int = 1) -> list[VectorMatch]:
        result = self._collection.query(
            query_embeddings=[embedding],
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        matches: list[VectorMatch] = []
        for cache_id, question, metadata, distance in zip(
            ids, documents, metadatas, distances, strict=True
        ):
            metadata = metadata or {}
            answer = str(metadata.get("answer", ""))
            public_metadata = {key: value for key, value in metadata.items() if key != "answer"}
            matches.append(
                VectorMatch(
                    cache_id=cache_id,
                    question=question,
                    answer=answer,
                    similarity=max(-1.0, min(1.0, 1.0 - float(distance))),
                    metadata=public_metadata,
                )
            )
        return matches

    def count(self) -> int:
        return self._collection.count()
