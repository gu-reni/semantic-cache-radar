import httpx
import pytest

from radar.app.collectors import HackerNewsCollector


@pytest.mark.asyncio
async def test_hackernews_collector_normalizes_top_stories() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[101, 102])
        if request.url.path.endswith("101.json"):
            return httpx.Response(
                200,
                json={"id": 101, "title": "Python release", "url": "https://example.com/python", "time": 1700000000},
            )
        return httpx.Response(200, json={"id": 102, "title": "No URL story", "time": 1700000001})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await HackerNewsCollector(client, story_limit=2).fetch()

    assert len(items) == 2
    assert items[0].source == "hackernews"
    assert items[0].external_id == "101"
    assert items[0].title == "Python release"
    assert items[1].url == "https://news.ycombinator.com/item?id=102"
