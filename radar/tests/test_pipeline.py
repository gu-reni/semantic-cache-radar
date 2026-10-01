from pathlib import Path

import httpx
import pytest

from radar.app.models import RadarItem
from radar.app.pipeline import GatewayEnricher, RadarPipeline
from radar.app.repository import RadarRepository


@pytest.mark.asyncio
async def test_pipeline_calls_gateway_and_stores_enrichment(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = request.content.decode()
        assert "Python release" in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"summary":"Python 发布了新版本。","tags":["Python","Release"]}'
                        }
                    }
                ]
            },
        )

    repository = RadarRepository(str(tmp_path / "radar.db"))
    item = RadarItem("hackernews", "1", "Python release", "https://example.com")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RadarPipeline(
            repository,
            GatewayEnricher(client, "http://gateway:8000"),
        ).process([item])

    saved = repository.list_items()
    assert result == type(result)(processed=1, stored=1, failed=0)
    assert saved[0].summary == "Python 发布了新版本。"
    assert saved[0].tags == ["Python", "Release"]


@pytest.mark.asyncio
async def test_prompt_carries_source_material_when_available(tmp_path: Path) -> None:
    """有原文素材时必须送进提示词。

    只送标题时，模型能写的只有「该标题探讨…」，看着像摘要其实什么都没说。
    这条测试守的是「素材有没有真的送到」—— 少送了不会报错，只是摘要变差。
    """
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"summary":"摘要","tags":["标签"]}'}}]},
        )

    repository = RadarRepository(str(tmp_path / "radar.db"))
    item = RadarItem(
        "v2ex",
        "9",
        "k20pro 有没有办法秒解 bl？",
        "https://www.v2ex.com/t/9",
        summary="正文：已经试过官方解锁工具，卡在等待 168 小时那一步。",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await RadarPipeline(
            repository, GatewayEnricher(client, "http://gateway:8000")
        ).process([item])

    assert "标题：k20pro 有没有办法秒解 bl？" in captured["body"]
    assert "卡在等待 168 小时那一步" in captured["body"], "素材没送到提示词里"
    assert "不要复述标题" in captured["body"]


@pytest.mark.asyncio
async def test_prompt_omits_material_block_when_there_is_none(tmp_path: Path) -> None:
    """没有素材时不要留下一个空的「原文（节选）：」，那会误导模型以为有内容。"""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"summary":"摘要","tags":["标签"]}'}}]},
        )

    repository = RadarRepository(str(tmp_path / "radar.db"))
    item = RadarItem("hackernews", "1", "Just a title", "https://example.com")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await RadarPipeline(
            repository, GatewayEnricher(client, "http://gateway:8000")
        ).process([item])

    assert "原文（节选）" not in captured["body"]


@pytest.mark.asyncio
async def test_pipeline_keeps_item_when_gateway_response_is_invalid(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    repository = RadarRepository(str(tmp_path / "radar.db"))
    item = RadarItem("v2ex", "2", "Original title", "https://example.com")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RadarPipeline(
            repository,
            GatewayEnricher(client, "http://gateway:8000"),
        ).process([item])

    assert result.processed == 1
    assert result.stored == 0
    assert result.failed == 1
    assert repository.list_items()[0].title == "Original title"
    assert repository.list_items()[0].summary is None
