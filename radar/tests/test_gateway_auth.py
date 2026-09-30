"""雷达调用网关时的鉴权头测试。

网关一旦配置共享令牌，雷达也必须带上，否则整条链路的摘要生成会被 401 拒掉。
这是跨服务的约定，容易被单独改动一侧时破坏，所以在这里钉住。
"""
import httpx
import pytest

from radar.app.models import RadarItem
from radar.app.pipeline import GatewayEnricher

ITEM = RadarItem(
    source="hackernews",
    external_id="1",
    title="A new database engine",
    url="https://example.com/1",
)

ENRICHED_BODY = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": '{"summary":"摘要","tags":["数据库"]}',
            }
        }
    ]
}


def _client_capturing(sent: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=ENRICHED_BODY)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_enricher_sends_token_when_configured() -> None:
    sent: list[httpx.Request] = []
    client = _client_capturing(sent)
    enricher = GatewayEnricher(client, "http://gateway:8000", auth_token="shared-secret")

    await enricher.enrich(ITEM)

    assert len(sent) == 1
    assert sent[0].headers["X-Gateway-Token"] == "shared-secret"


@pytest.mark.asyncio
async def test_enricher_omits_token_when_not_configured() -> None:
    """本地开发时网关不校验，此时不该凭空造一个空令牌头。"""
    sent: list[httpx.Request] = []
    client = _client_capturing(sent)
    enricher = GatewayEnricher(client, "http://127.0.0.1:8000")

    await enricher.enrich(ITEM)

    assert len(sent) == 1
    assert "X-Gateway-Token" not in sent[0].headers


@pytest.mark.asyncio
async def test_enricher_strips_trailing_slash_from_gateway_url() -> None:
    sent: list[httpx.Request] = []
    client = _client_capturing(sent)
    enricher = GatewayEnricher(client, "http://gateway:8000/")

    await enricher.enrich(ITEM)

    assert str(sent[0].url) == "http://gateway:8000/v1/chat/completions"
