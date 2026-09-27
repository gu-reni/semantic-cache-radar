from pathlib import Path

from gateway.app.vector_store import VectorStore


def test_vector_store_adds_and_searches_entry(tmp_path: Path) -> None:
    store = VectorStore(str(tmp_path / "chroma"))
    cache_id = store.add(
        question="什么是语义缓存？",
        answer="复用相似问题的历史回答。",
        embedding=[1.0, 0.0, 0.0],
        metadata={"model": "test-model"},
    )

    matches = store.search([1.0, 0.0, 0.0])

    assert cache_id
    assert store.count() == 1
    assert len(matches) == 1
    assert matches[0].cache_id == cache_id
    assert matches[0].question == "什么是语义缓存？"
    assert matches[0].answer == "复用相似问题的历史回答。"
    assert matches[0].similarity == 1.0
    assert matches[0].metadata["model"] == "test-model"


def test_vector_store_returns_empty_for_empty_collection(tmp_path: Path) -> None:
    store = VectorStore(str(tmp_path / "chroma"))

    assert store.search([1.0, 0.0, 0.0]) == []
