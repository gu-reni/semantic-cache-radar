import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from bs4 import BeautifulSoup

from radar.app.models import RadarItem


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
                published_at=str(story.get("time")) if story.get("time") else None,
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
            items.append(
                RadarItem(
                    source="v2ex",
                    external_id=str(topic_id),
                    title=str(title),
                    url=str(topic.get("url") or f"https://www.v2ex.com/t/{topic_id}"),
                    published_at=str(topic.get("created")) if topic.get("created") else None,
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
                )
            )
        return items


async def _gather_limited(
    values: list[int],
    function: Callable[[int], Awaitable[RadarItem | None]],
) -> list[RadarItem | None]:
    """Run collector calls concurrently while preserving input order."""
    return list(await asyncio.gather(*(function(value) for value in values)))
