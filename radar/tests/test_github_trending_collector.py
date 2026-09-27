import httpx
import pytest

from radar.app.collectors import GitHubTrendingCollector


@pytest.mark.asyncio
async def test_github_trending_collector_parses_repository_cards() -> None:
    html = """
    <main>
      <article class="Box-row">
        <h2><a href="/owner/project">owner / project</a></h2>
        <p>A useful project description.</p>
      </article>
      <article class="Box-row">
        <h2><a href="/team/tool/">team / tool</a></h2>
      </article>
      <article class="Box-row"><p>Invalid card</p></article>
    </main>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/trending"
        assert request.headers["user-agent"] == "semantic-cache-radar/0.1"
        return httpx.Response(200, text=html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await GitHubTrendingCollector(client, repository_limit=3).fetch()

    assert len(items) == 2
    assert items[0].source == "github-trending"
    assert items[0].external_id == "owner/project"
    assert items[0].title == "owner project"
    assert items[0].url == "https://github.com/owner/project"
    assert items[0].summary == "A useful project description."
    assert items[1].external_id == "team/tool"
    assert items[1].summary is None
