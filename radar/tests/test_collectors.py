from datetime import datetime

import httpx
import pytest

from radar.app.collectors import HackerNewsCollector, _unix_to_iso


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


@pytest.mark.asyncio
async def test_hackernews_published_at_is_self_describing_iso_timestamp() -> None:
    """published_at 必须是自解释的 ISO 字符串，不能是裸的 Unix 秒。

    原先塞的是 str(1700000000)。前端 new Date('1700000000') 会被当成
    「年份 1700000000」而得到 Invalid Date，条目上的时间就成了空白，
    而接口本身返回 200、看不出任何异常 —— 只有在页面上逐条核对才会发现。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[101])
        return httpx.Response(200, json={"id": 101, "title": "T", "time": 1700000000})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await HackerNewsCollector(client, story_limit=1).fetch()

    published = items[0].published_at
    assert published is not None
    assert published != "1700000000", "不能把 Unix 秒直接当字符串塞进去"
    parsed = datetime.fromisoformat(published)
    assert parsed.tzinfo is not None, "必须带时区，否则消费方只能按本地时区猜"
    assert int(parsed.timestamp()) == 1700000000


@pytest.mark.parametrize("value", [None, "", 0, "not-a-number", 10**20])
def test_unix_to_iso_returns_none_for_unusable_values(value: object) -> None:
    """拿不到可用时间就返回 None，不要编一个假时间出来。"""
    assert _unix_to_iso(value) is None
