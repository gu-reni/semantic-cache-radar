import json
import logging
from dataclasses import dataclass, replace
from typing import Any

import httpx

from radar.app.models import RadarItem
from radar.app.repository import RadarRepository
from radar.app.semantic import SemanticLinker

logger = logging.getLogger(__name__)


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
        material = f"标题：{item.title}"
        if item.summary:
            # 采集阶段抓到的原文素材（V2EX 的正文、GitHub 的仓库描述）。
            # 之前提示词里只有标题，模型没别的可说，于是写出
            # 「该标题探讨…」「关于 X 的技术资讯标题」这类复述标题的摘要 ——
            # 那不是模型不行，是没给它料。
            material += f"\n原文（节选）：{item.summary}"
        prompt = (
            "请根据下面的技术资讯，只返回 JSON，不要 Markdown。"
            '格式必须是 {"summary":"一句话摘要","tags":["标签1","标签2"]}。\n'
            "摘要要说明它讲的是什么，不要复述标题；只依据给定材料，不要补充材料里没有的细节。\n"
            f"{material}"
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
    def __init__(
        self,
        repository: RadarRepository,
        enricher: GatewayEnricher,
        linker: SemanticLinker | None = None,
    ) -> None:
        self._repository = repository
        self._enricher = enricher
        self._linker = linker

    async def process(self, items: list[RadarItem]) -> EnrichmentResult:
        stored = 0
        failed = 0
        for item in items:
            try:
                enriched = await self._enricher.enrich(item)
                item_id = self._repository.upsert(enriched)
                stored += 1
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                failed += 1
                item_id = self._repository.upsert(item)
            # 语义归并是观测性增强，失败绝不能连累条目落库：
            # 算不出相似度就跳过，条目照常存在（与 enrich 失败时保留条目的做法一致）。
            if self._linker is not None:
                try:
                    await self._linker.link(item_id, item.title)
                except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    logger.warning("语义归并失败，跳过该条目 item_id=%s", item_id)
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
