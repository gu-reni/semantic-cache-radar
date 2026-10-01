"""跨源语义归并的单元测试。

钉住三件事：
1. 余弦相似度用标准库算对（不引新依赖）；
2. 疑似同源候选只在相似度 >= 观察下限时记录，且把相似度数值一起落库；
3. 网关算不出向量时降级：不记候选，也不把整批弄挂。
"""

import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from radar.app.models import RadarItem
from radar.app.pipeline import GatewayEnricher, RadarPipeline
from radar.app.repository import RadarRepository
from radar.app.semantic import SemanticLinker, cosine_similarity


def test_cosine_similarity_known_values() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([3.0, 4.0], [3.0, 4.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)


def test_cosine_similarity_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        cosine_similarity([1.0], [1.0, 2.0])


def _links(repository: RadarRepository) -> list[sqlite3.Row]:
    with sqlite3.connect(repository._database_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute("SELECT * FROM semantic_links").fetchall()


@pytest.mark.asyncio
async def test_linker_records_candidates_above_observation_floor(tmp_path: Path) -> None:
    """相似度 >= 观察下限的配对要连同数值一起落库。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    similar_id = repository.upsert(
        RadarItem("hackernews", "1", "similar-one", "https://example.com/a")
    )
    repository.upsert(RadarItem("v2ex", "2", "unrelated", "https://example.com/b"))
    new_id = repository.upsert(
        RadarItem("github-trending", "a/b", "similar-two", "https://github.com/a/b")
    )

    # 新标题与 similar-one 同向量（相似度 1.0），与 unrelated 正交（相似度 0.0）。
    vectors = {
        "similar-two": [1.0, 0.0],
        "similar-one": [1.0, 0.0],
        "unrelated": [0.0, 1.0],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        data = [
            {"index": i, "embedding": vectors.get(text, [0.0, 0.0])}
            for i, text in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await SemanticLinker(client, "http://gateway:8000", repository).link(
            new_id, "similar-two"
        )

    rows = _links(repository)
    assert len(rows) == 1, "只有相似的那一对该被记下来"
    assert rows[0]["item_id"] == new_id
    assert rows[0]["other_item_id"] == similar_id
    assert rows[0]["similarity"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_linker_skips_pairs_below_observation_floor(tmp_path: Path) -> None:
    """相似度低于观察下限的配对不该被记下来。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(RadarItem("hackernews", "1", "unrelated", "https://example.com/a"))
    new_id = repository.upsert(RadarItem("v2ex", "2", "new", "https://example.com/b"))

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # 新标题 [1,0]，近期标题 [0,1]（正交，相似度 0）。
        data = [
            {"index": i, "embedding": [1.0, 0.0] if i == 0 else [0.0, 1.0]}
            for i in range(len(body["input"]))
        ]
        return httpx.Response(200, json={"data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await SemanticLinker(client, "http://gateway:8000", repository).link(new_id, "new")

    assert _links(repository) == []


@pytest.mark.asyncio
async def test_linker_requests_symmetric_embeddings_with_token(tmp_path: Path) -> None:
    """标题对标题走 symmetric，且必须带共享令牌。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(RadarItem("hackernews", "1", "recent", "https://example.com/a"))
    new_id = repository.upsert(RadarItem("v2ex", "2", "new", "https://example.com/b"))

    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        body = json.loads(request.content)
        data = [
            {"index": i, "embedding": [1.0, 0.0]} for i in range(len(body["input"]))
        ]
        return httpx.Response(200, json={"data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await SemanticLinker(
            client, "http://gateway:8000", repository, auth_token="shared-secret"
        ).link(new_id, "new")

    assert len(sent) == 1
    assert sent[0].headers["X-Gateway-Token"] == "shared-secret"
    assert json.loads(sent[0].content)["input_type"] == "symmetric"


@pytest.mark.asyncio
async def test_pipeline_keeps_item_when_semantic_linking_fails(tmp_path: Path) -> None:
    """网关算不出向量时，条目必须照常落库，不能整批失败。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    # 先写一条近期条目，让 linker 有东西可比、才会真的去调 /v1/embeddings。
    repository.upsert(RadarItem("hackernews", "0", "existing", "https://example.com/0"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"summary":"摘要","tags":["标签"]}'}}
                    ]
                },
            )
        return httpx.Response(500)

    item = RadarItem("v2ex", "2", "Original title", "https://example.com/2")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RadarPipeline(
            repository,
            GatewayEnricher(client, "http://gateway:8000"),
            SemanticLinker(client, "http://gateway:8000", repository),
        ).process([item])

    assert result.processed == 1
    assert result.stored == 1
    assert result.failed == 0
    saved = [i for i in repository.list_items() if i.external_id == "2"]
    assert saved[0].summary == "摘要", "富化结果不能因为语义归并失败而丢失"
