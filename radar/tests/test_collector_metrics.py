"""采集器必须把源站给的量化信号留下来。

这些数字源站本来就在返回，原先采集器只取标题和链接、其余全部丢弃。
丢掉的后果是数据层再也答不了「这个项目最近是不是在涨」——
而那是「雷达」和「资讯列表」的全部区别。
"""

import httpx
import pytest

from radar.app.collectors import (
    GitHubTrendingCollector,
    HackerNewsCollector,
    V2EXCollector,
    _parse_count,
)


@pytest.mark.asyncio
async def test_hackernews_keeps_points_comments_and_author() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[101])
        return httpx.Response(
            200,
            json={
                "id": 101,
                "title": "Show HN: something",
                "url": "https://example.com/a",
                "time": 1700000000,
                "score": 153,
                "descendants": 31,
                "by": "someone",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await HackerNewsCollector(client, story_limit=1).fetch()

    assert items[0].metrics == {"points": 153, "comments": 31, "author": "someone"}


@pytest.mark.asyncio
async def test_hackernews_omits_metric_keys_the_api_did_not_return() -> None:
    """字段缺失就留空，不要填 0 —— 0 会被后面的排序当成「热度为零」。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[101])
        return httpx.Response(200, json={"id": 101, "title": "T", "time": 1700000000})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await HackerNewsCollector(client, story_limit=1).fetch()

    assert items[0].metrics is None


@pytest.mark.asyncio
async def test_v2ex_keeps_replies_node_and_author() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1071234,
                    "title": "某话题",
                    "url": "https://www.v2ex.com/t/1071234",
                    "created": 1700000000,
                    "replies": 50,
                    "node": {"name": "程序员", "title": "程序员"},
                    "member": {"username": "someone"},
                    "last_touched": 1700000500,
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await V2EXCollector(client, topic_limit=1).fetch()

    metrics = items[0].metrics
    assert metrics["replies"] == 50
    assert metrics["node"] == "程序员"
    assert metrics["author"] == "someone"
    assert metrics["last_touched"] == "2023-11-14T22:21:40+00:00"


@pytest.mark.asyncio
async def test_github_trending_keeps_stars_today_and_language() -> None:
    card_html = """
    <html><body>
      <article class="Box-row">
        <h2 class="h3 lh-condensed"><a href="/psf/requests">psf / requests</a></h2>
        <p class="col-9">A simple HTTP library.</p>
        <span itemprop="programmingLanguage">Python</span>
        <a href="/psf/requests/stargazers">149,832</a>
        <span class="float-sm-right">1,179 stars today</span>
      </article>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=card_html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await GitHubTrendingCollector(client, repository_limit=1).fetch()

    assert items[0].metrics == {"stars": 149832, "stars_today": 1179, "language": "Python"}
    assert items[0].external_id == "psf/requests"


def test_parse_count_handles_thousands_separators_and_labels() -> None:
    assert _parse_count("149,832") == 149832
    assert _parse_count("1,179 stars today") == 1179
    assert _parse_count("5 stars today") == 5


@pytest.mark.parametrize("value", [None, "", "no digits here"])
def test_parse_count_returns_none_when_there_is_no_number(value: str | None) -> None:
    assert _parse_count(value) is None
