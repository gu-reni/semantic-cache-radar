import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from radar.app.models import RadarItem


def _unix_to_iso(value: Any) -> str | None:
    """把 Unix 秒转成 ISO-8601（UTC），拿不到就返回 None。

    为什么统一在这里转：
      RadarItem.published_at 声明是 str | None，它就该是自解释的 ISO 字符串。
      原先把 HN 与 V2EX 的 Unix 时间戳直接 str() 塞进去，于是同一个字段
      有的源是 Unix 秒、有的源是 None，接口语义不自洽。前端
      new Date('1790648874') 会被当成「年份 1790648874」而变成 Invalid Date，
      时间是空白的 —— 而且这个坑每个消费者都要再踩一次。
    """
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_count(text: str | None) -> int | None:
    """从 `149,832` 或 `1,179 stars today` 这类文案里取出数字。"""
    if not text:
        return None
    match = re.search(r"[\d,]+", text)
    if match is None:
        return None
    return _as_int(match.group(0).replace(",", ""))


def _metrics(**values: Any) -> dict[str, Any] | None:
    """丢掉取不到的值，剩下的存下来；一个都没有就返回 None。

    不做默认值填充：源站没给就是没给，写 0 会把「没有这个信号」伪装成
    「这个信号等于零」，而后者会直接影响后续的排序与评级。
    """
    cleaned = {key: value for key, value in values.items() if value not in (None, "")}
    return cleaned or None


class HackerNewsCollector:
    """Collect recent top stories from the Hacker News Firebase API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        story_limit: int = 10,
    ) -> None:
        self._client = client
        self._story_limit = story_limit

    async def fetch(self) -> list[RadarItem]:
        response = await self._client.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json"
        )
        response.raise_for_status()
        story_ids = response.json()[: self._story_limit]

        async def fetch_story(story_id: int) -> RadarItem | None:
            story_response = await self._client.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
            )
            story_response.raise_for_status()
            story: dict[str, Any] = story_response.json()
            title = story.get("title")
            if not title:
                return None
            return RadarItem(
                source="hackernews",
                external_id=str(story_id),
                title=str(title),
                url=str(story.get("url") or f"https://news.ycombinator.com/item?id={story_id}"),
                published_at=_unix_to_iso(story.get("time")),
                metrics=_metrics(
                    points=_as_int(story.get("score")),
                    comments=_as_int(story.get("descendants")),
                    author=story.get("by"),
                ),
            )

        results = await _gather_limited(story_ids, fetch_story)
        return [item for item in results if item is not None]


class V2EXCollector:
    """Collect recent topics from the public V2EX topics API."""

    def __init__(self, client: httpx.AsyncClient, topic_limit: int = 10) -> None:
        self._client = client
        self._topic_limit = topic_limit

    async def fetch(self) -> list[RadarItem]:
        response = await self._client.get("https://www.v2ex.com/api/topics/latest.json")
        response.raise_for_status()
        topics = response.json()[: self._topic_limit]

        items: list[RadarItem] = []
        for topic in topics:
            topic_id = topic.get("id")
            title = topic.get("title")
            if topic_id is None or not title:
                continue
            node = topic.get("node")
            member = topic.get("member")
            items.append(
                RadarItem(
                    source="v2ex",
                    external_id=str(topic_id),
                    title=str(title),
                    url=str(topic.get("url") or f"https://www.v2ex.com/t/{topic_id}"),
                    published_at=_unix_to_iso(topic.get("created")),
                    metrics=_metrics(
                        replies=_as_int(topic.get("replies")),
                        node=node.get("name") if isinstance(node, dict) else None,
                        author=member.get("username") if isinstance(member, dict) else None,
                        last_touched=_unix_to_iso(topic.get("last_touched")),
                    ),
                )
            )
        return items


class GitHubTrendingCollector:
    """Parse repository cards from GitHub's public Trending page."""

    def __init__(self, client: httpx.AsyncClient, repository_limit: int = 10) -> None:
        self._client = client
        self._repository_limit = repository_limit

    async def fetch(self) -> list[RadarItem]:
        response = await self._client.get(
            "https://github.com/trending",
            headers={"Accept": "text/html", "User-Agent": "semantic-cache-radar/0.1"},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        items: list[RadarItem] = []

        for article in soup.select("article.Box-row")[: self._repository_limit]:
            link = article.select_one("h2 a")
            if link is None:
                continue
            path = " ".join(link.get("href", "").split())
            path = path.strip("/")
            if "/" not in path:
                continue
            description_node = article.select_one("p")
            stars_node = article.select_one('a[href$="/stargazers"]')
            today_node = article.select_one("span.float-sm-right")
            language_node = article.select_one("[itemprop=programmingLanguage]")
            title = path.replace("/", " ", 1)
            items.append(
                RadarItem(
                    source="github-trending",
                    external_id=path,
                    title=title,
                    url=f"https://github.com/{path}",
                    summary=description_node.get_text(" ", strip=True)
                    if description_node
                    else None,
                    metrics=_metrics(
                        stars=_parse_count(
                            stars_node.get_text(strip=True) if stars_node else None
                        ),
                        stars_today=_parse_count(
                            today_node.get_text(strip=True) if today_node else None
                        ),
                        language=language_node.get_text(strip=True) if language_node else None,
                    ),
                )
            )
        return items


async def _gather_limited(
    values: list[int],
    function: Callable[[int], Awaitable[RadarItem | None]],
) -> list[RadarItem | None]:
    """Run collector calls concurrently while preserving input order."""
    return list(await asyncio.gather(*(function(value) for value in values)))
