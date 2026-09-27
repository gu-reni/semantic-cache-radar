import pytest

from radar.app.models import RadarItem
from radar.app.pipeline import EnrichmentResult
from radar.app.runner import RadarRunner


class FakeCollector:
    def __init__(self, items: list[RadarItem] | None = None, error: Exception | None = None) -> None:
        self._items = items or []
        self._error = error

    async def fetch(self) -> list[RadarItem]:
        if self._error:
            raise self._error
        return self._items


class FakePipeline:
    def __init__(self) -> None:
        self.received: list[RadarItem] = []

    async def process(self, items: list[RadarItem]) -> EnrichmentResult:
        self.received = items
        return EnrichmentResult(processed=len(items), stored=len(items), failed=0)


@pytest.mark.asyncio
async def test_runner_isolates_failed_source() -> None:
    item = RadarItem("hackernews", "1", "Title", "https://example.com")
    pipeline = FakePipeline()
    result = await RadarRunner(
        [FakeCollector([item]), FakeCollector(error=ValueError("bad response"))],
        pipeline,
    ).run_once()

    assert result.fetched == 1
    assert result.source_failures == 1
    assert result.processed == 1
    assert pipeline.received == [item]
