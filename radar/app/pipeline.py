import json
from dataclasses import dataclass, replace
from typing import Any

import httpx

from radar.app.models import RadarItem
from radar.app.repository import RadarRepository


@dataclass(frozen=True)
class EnrichmentResult:
    processed: int
    stored: int
    failed: int


class GatewayEnricher:
    """Generate a summary and tags through the semantic cache gateway."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        gateway_url: str,
        auth_token: str | None = None,
    ) -> None:
        self._client = client
        self._gateway_url = gateway_url.rstrip("/")
        # 网关若配置了共享令牌，雷达也必须带上，否则会被 401 拒掉。
        self._headers = {"X-Gateway-Token": auth_token} if auth_token else {}

    async def enrich(self, item: RadarItem) -> RadarItem:
        prompt = (
            "请分析下面的技术资讯标题，只返回 JSON，不要 Markdown。"
            '格式必须是 {"summary":"一句话摘要","tags":["标签1","标签2"]}。\n'
            f"标题：{item.title}"
        )
        response = await self._client.post(
            f"{self._gateway_url}/v1/chat/completions",
            headers={**self._headers, "X-Cache-Mode": "exact"},
            json={
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 256,
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        enrichment = _parse_enrichment(content)
        # 用 replace 而不是把字段一个个重新拼一遍：
        # 手拼的写法在加新字段时不会报错，只会把那个字段悄悄丢掉
        # （metrics 就是这么差一点被丢的；已用反向验证确认这条测试守得住）。
        return replace(
            item,
            summary=enrichment["summary"],
            tags=enrichment["tags"],
        )


class RadarPipeline:
    def __init__(self, repository: RadarRepository, enricher: GatewayEnricher) -> None:
        self._repository = repository
        self._enricher = enricher

    async def process(self, items: list[RadarItem]) -> EnrichmentResult:
        stored = 0
        failed = 0
        for item in items:
            try:
                enriched = await self._enricher.enrich(item)
                self._repository.upsert(enriched)
                stored += 1
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                failed += 1
                self._repository.upsert(item)
        return EnrichmentResult(processed=len(items), stored=stored, failed=failed)


def _parse_enrichment(content: str) -> dict[str, Any]:
    data = json.loads(content.strip())
    summary = data.get("summary")
    tags = data.get("tags")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must be a non-empty string")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of strings")
    return {"summary": summary.strip(), "tags": tags}
