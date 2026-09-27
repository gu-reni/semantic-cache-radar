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
