import logging

import httpx
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


@pytest.mark.asyncio
async def test_runner_names_the_sources_that_failed() -> None:
    """只记「挂了几个」不够，还要记「挂的是谁」。

    线上看到 source_failures=2 时，得能直接知道是哪两个源，
    而不是回去逐个手工探测。
    """
    result = await RadarRunner(
        [
            FakeCollector([RadarItem("hackernews", "1", "T", "https://e.com")]),
            FakeCollector(error=ValueError("bad response")),
            FakeCollector(error=httpx.ConnectTimeout("")),
        ],
        FakePipeline(),
    ).run_once()

    assert result.source_failures == 2
    assert result.failed_sources == ("FakeCollector", "FakeCollector")


@pytest.mark.asyncio
async def test_runner_logs_usable_error_for_exceptions_with_empty_message(caplog) -> None:
    """httpx 的超时异常 str() 是空串，只打 %s 会得到一行没有信息的日志。

    实测在 ECS 上就撞到过：日志只有 "collector failed:"，既不知道是哪个源，
    也不知道原因（V2EX 与 GitHub 在那边直连都不通）。
    所以必须用 %r 把异常类型带出来。
    """

    class V2EXCollector(FakeCollector):
        pass

    with caplog.at_level(logging.WARNING):
        result = await RadarRunner(
            [V2EXCollector(error=httpx.ConnectTimeout(""))],
            FakePipeline(),
        ).run_once()

    assert result.failed_sources == ("V2EXCollector",)
    assert "V2EXCollector" in caplog.text, "日志必须带源名"
    assert "ConnectTimeout" in caplog.text, "异常类型为空时必须靠 %r 带出类型"
