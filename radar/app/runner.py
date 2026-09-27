import argparse
import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from radar.app.collectors import GitHubTrendingCollector, HackerNewsCollector, V2EXCollector
from radar.app.pipeline import EnrichmentResult, GatewayEnricher, RadarPipeline
from radar.app.repository import RadarRepository

logger = logging.getLogger(__name__)


class Collector(Protocol):
    async def fetch(self) -> list:
        ...


@dataclass(frozen=True)
class RadarRunResult:
    fetched: int
    processed: int
    stored: int
    failed: int
    source_failures: int


class RadarRunner:
    def __init__(
        self,
        collectors: list[Collector],
        pipeline: RadarPipeline,
    ) -> None:
        self._collectors = collectors
        self._pipeline = pipeline

    async def run_once(self) -> RadarRunResult:
        items = []
        source_failures = 0
        for collector in self._collectors:
            try:
                items.extend(await collector.fetch())
            except (httpx.HTTPError, ValueError) as exc:
                source_failures += 1
                logger.warning("collector failed: %s", exc)

        enrichment: EnrichmentResult = await self._pipeline.process(items)
        return RadarRunResult(
            fetched=len(items),
            processed=enrichment.processed,
            stored=enrichment.stored,
            failed=enrichment.failed,
            source_failures=source_failures,
        )


async def run_configured_once() -> RadarRunResult:
    gateway_url = os.getenv("GATEWAY_BASE_URL", "http://127.0.0.1:8000")
    database_path = os.getenv("RADAR_DATABASE_PATH", "./data/radar.db")
    story_limit = int(os.getenv("RADAR_ITEM_LIMIT", "10"))

    async with httpx.AsyncClient(timeout=20.0) as client:
        collectors = [
            HackerNewsCollector(client, story_limit=story_limit),
            V2EXCollector(client, topic_limit=story_limit),
            GitHubTrendingCollector(client, repository_limit=story_limit),
        ]
        pipeline = RadarPipeline(
            RadarRepository(database_path),
            GatewayEnricher(client, gateway_url),
        )
        return await RadarRunner(collectors, pipeline).run_once()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one technical radar collection job")
    parser.add_argument("--once", action="store_true", help="run one collection job")
    args = parser.parse_args()
    if not args.once:
        parser.error("only --once is currently supported")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = asyncio.run(run_configured_once())
    print(result)


if __name__ == "__main__":
    main()
