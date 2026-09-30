"""X-Cache-Mode 请求头的行为测试。

为什么这个头重要：
  语义缓存按向量相似度复用答案，前提是「问题不同则语义不同」。
  雷达的提示词里模板占绝大部分篇幅，只有标题在变，
  整批请求的相似度都会越过阈值，导致 20 条资讯共用同一个摘要（实测命中率 19/20）。
  所以调用方必须能声明「只按文本完全相同复用」。
"""
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gateway.app.cache_service import CacheResult, GeneratedAnswer
from gateway.app.main import app, get_llm_client, get_semantic_cache

BODY = {"messages": [{"role": "user", "content": "请分析标题：某条资讯"}]}


class FakeLLMClient:
    async def chat_completion(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


class RecordingSemanticCache:
    """记录收到的 cache_mode，用来验证请求头确实透传到了缓存层。"""

    def __init__(self) -> None:
        self.cache_modes: list[str] = []

    async def get_or_create(
        self,
        question: str,
        generate_answer: Any,
        metadata: Any,
        cache_mode: str = "semantic",
    ) -> CacheResult:
        self.cache_modes.append(cache_mode)
        generated = await generate_answer()
        assert isinstance(generated, GeneratedAnswer)
        return CacheResult(answer=generated.answer, hit=False, cache_id="id", usage=generated.usage)


@pytest.fixture
def recording_cache():
    cache = RecordingSemanticCache()
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient()
    app.dependency_overrides[get_semantic_cache] = lambda: cache
    yield cache
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("header_value", "expected"),
    [
        (None, "semantic"),
        ("semantic", "semantic"),
        ("exact", "exact"),
        ("EXACT", "exact"),
        (" exact ", "exact"),
        ("Exact", "exact"),
        ("unknown-value", "semantic"),
        ("", "semantic"),
    ],
)
def test_cache_mode_header_is_normalized(
    recording_cache: RecordingSemanticCache, header_value: str | None, expected: str
) -> None:
    headers = {} if header_value is None else {"X-Cache-Mode": header_value}
    response = TestClient(app).post("/v1/chat/completions", json=BODY, headers=headers)

    assert response.status_code == 200
    assert recording_cache.cache_modes == [expected]


def test_radar_style_request_uses_exact_mode(recording_cache: RecordingSemanticCache) -> None:
    """雷达采集走的正是这条路径：必须落到 exact，否则整批共用同一个摘要。"""
    response = TestClient(app).post(
        "/v1/chat/completions",
        json=BODY,
        headers={"X-Cache-Mode": "exact"},
    )

    assert response.status_code == 200
    assert recording_cache.cache_modes == ["exact"]
