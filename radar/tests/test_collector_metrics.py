"""采集器必须把源站给的量化信号留下来。

这些数字源站本来就在返回，原先采集器只取标题和链接、其余全部丢弃。
丢掉的后果是数据层再也答不了「这个项目最近是不是在涨」——
而那是「雷达」和「资讯列表」的全部区别。
"""

import httpx
import pytest

from radar.app.collectors import (
    BODY_LIMIT,
    GitHubTrendingCollector,
    HackerNewsCollector,
    V2EXCollector,
    _parse_count,
    _truncate,
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


@pytest.mark.asyncio
async def test_v2ex_keeps_the_post_body_as_source_material() -> None:
    """V2EX 的正文必须留下来当写摘要的素材。

    标题常常只是个引子（「k20pro 有没有办法秒解 bl？」），内容在正文里。
    只给标题时，摘要只能写成「该标题探讨…」这种复述标题的话 ——
    不是模型不行，是没给它料。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1071234,
                    "title": "某话题",
                    "url": "https://www.v2ex.com/t/1071234",
                    "created": 1700000000,
                    "content": "正文第一段。\n\n正文第二段，这里才是真正的信息。",
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await V2EXCollector(client, topic_limit=1).fetch()

    assert items[0].summary is not None
    assert "正文第二段" in items[0].summary
    assert "\n" not in items[0].summary, "换行应被压平，避免撑坏提示词结构"


@pytest.mark.asyncio
async def test_v2ex_body_is_truncated() -> None:
    """正文有的几千字，而下游只要一句摘要；传全文只涨成本不提质量。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "title": "很长的话题",
                    "url": "https://www.v2ex.com/t/1",
                    "created": 1700000000,
                    "content": "甲" * 5000,
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await V2EXCollector(client, topic_limit=1).fetch()

    body = items[0].summary
    assert body is not None
    assert len(body) <= BODY_LIMIT + 1, f"应截断到 {BODY_LIMIT} 附近，实际 {len(body)}"
    assert body.endswith("…"), "截断要留痕，别让人以为原文就这么长"


@pytest.mark.asyncio
async def test_v2ex_without_body_leaves_summary_empty() -> None:
    """没有正文就留空，不要拿标题去顶替 —— 那会让下游以为有素材。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"id": 1, "title": "无正文", "url": "https://www.v2ex.com/t/1", "created": 1700000000}],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await V2EXCollector(client, topic_limit=1).fetch()

    assert items[0].summary is None


@pytest.mark.parametrize("value", [None, "", "   "])
def test_truncate_returns_none_for_empty_input(value: str | None) -> None:
    assert _truncate(value) is None


@pytest.mark.parametrize("value", [None, "", "no digits here"])
def test_parse_count_returns_none_when_there_is_no_number(value: str | None) -> None:
    assert _parse_count(value) is None
