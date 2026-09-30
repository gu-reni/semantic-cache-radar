import argparse
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Protocol

import httpx

from radar.app.collectors import (
    GitHubTrendingCollector,
    HackerNewsCollector,
    V2EXCollector,
)
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
    # 具体是哪几个源挂了。只记计数的话，线上看到「挂了 2 个」还得逐个手工探测才能
    # 知道是谁，而日志里 httpx 超时异常的 str() 本身是空串，等于什么线索都没有。
    failed_sources: tuple[str, ...] = ()


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
        failed_sources: list[str] = []
        for collector in self._collectors:
            name = type(collector).__name__
            try:
                items.extend(await collector.fetch())
            except (httpx.HTTPError, ValueError) as exc:
                failed_sources.append(name)
                # 用 %r 而不是 %s：httpx 的超时异常 str() 是空串，
                # 只打 %s 只会得到一行没有任何信息的 "collector failed:"。
                logger.warning("采集源失败 source=%s error=%r", name, exc)

        enrichment: EnrichmentResult = await self._pipeline.process(items)
        return RadarRunResult(
            fetched=len(items),
            processed=enrichment.processed,
            stored=enrichment.stored,
            failed=enrichment.failed,
            source_failures=len(failed_sources),
            failed_sources=tuple(failed_sources),
        )


async def run_configured_once() -> RadarRunResult:
    gateway_url = os.getenv("GATEWAY_BASE_URL", "http://127.0.0.1:8000")
    database_path = os.getenv("RADAR_DATABASE_PATH", "./data/radar.db")
    story_limit = int(os.getenv("RADAR_ITEM_LIMIT", "10"))
    # 与网关共用同一个共享令牌；网关未配置令牌时这里留空即可。
    gateway_token = os.getenv("GATEWAY_AUTH_TOKEN") or None

    async with httpx.AsyncClient(timeout=20.0) as client:
        collectors = [
            HackerNewsCollector(client, story_limit=story_limit),
            V2EXCollector(client, topic_limit=story_limit),
            GitHubTrendingCollector(client, repository_limit=story_limit),
        ]
        pipeline = RadarPipeline(
            RadarRepository(database_path),
            GatewayEnricher(client, gateway_url, auth_token=gateway_token),
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
