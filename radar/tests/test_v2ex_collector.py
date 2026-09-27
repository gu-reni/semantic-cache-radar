import httpx
import pytest

from radar.app.collectors import V2EXCollector


@pytest.mark.asyncio
async def test_v2ex_collector_normalizes_topics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/topics/latest.json"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 201,
                    "title": "Python 开发讨论",
                    "url": "https://www.v2ex.com/t/201",
                    "created": 1700000000,
                },
                {"id": 202, "title": "没有自定义链接"},
                {"id": 203},
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await V2EXCollector(client, topic_limit=3).fetch()

    assert len(items) == 2
    assert items[0].source == "v2ex"
    assert items[0].external_id == "201"
    assert items[0].title == "Python 开发讨论"
    assert items[1].url == "https://www.v2ex.com/t/202"
