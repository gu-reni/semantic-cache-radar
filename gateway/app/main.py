import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response

from gateway.app.cache_service import GeneratedAnswer, SemanticCache
from gateway.app.config import get_settings
from gateway.app.embeddings import EmbeddingService
from gateway.app.llm_client import LLMClient
from gateway.app.schemas import (
    ChatCompletionRequest,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingVector,
)
from gateway.app.vector_store import VectorStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """未配置令牌时给出显式告警，避免把不设防的网关部署到公网。"""
    if not get_settings().gateway_auth_token:
        logger.warning(
            "GATEWAY_AUTH_TOKEN 未配置：/v1/chat/completions 与 DELETE /cache 不校验身份。"
            "仅限本地开发；部署到公网机器前必须设置该变量。"
        )
    yield


app = FastAPI(
    title="Semantic Cache Gateway",
    version="0.1.0",
    lifespan=lifespan,
)


def require_gateway_token(
    authorization: Annotated[str | None, Header()] = None,
    x_gateway_token: Annotated[str | None, Header()] = None,
) -> None:
    """校验调用方身份。

    网关持有上游大模型的 API Key，若不校验身份又对外可达，
    等于把这个 Key 借给任何扫到端口的人去刷 Token。
    令牌可放在 Authorization: Bearer <token> 或 X-Gateway-Token 头里。
    """
    expected = get_settings().gateway_auth_token
    if not expected:
        # 未配置时不校验，启动时已打过告警。
        return

    provided = x_gateway_token
    if not provided and authorization:
        provided = authorization.removeprefix("Bearer ").strip()

    # 用恒定时间比较，避免通过响应耗时逐字节猜出令牌。
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing gateway token")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Return a lightweight liveness response without external dependencies."""
    return {"status": "ok"}


def get_llm_client() -> LLMClient:
    return LLMClient(get_settings())


@lru_cache
def get_semantic_cache() -> SemanticCache:
    settings = get_settings()
    return SemanticCache(
        embedding_service=EmbeddingService(settings),
        vector_store=VectorStore(settings.vector_store_path),
        similarity_threshold=settings.cache_similarity_threshold,
        ttl_seconds=settings.cache_ttl_seconds,
    )


@app.get("/cache/stats")
async def cache_stats(
    cache: Annotated[SemanticCache, Depends(get_semantic_cache)],
) -> dict[str, float | int]:
    stats = cache.stats()
    return {
        "requests": stats.requests,
        "hits": stats.hits,
        "misses": stats.misses,
        "hit_rate": stats.hit_rate,
        "stored_entries": stats.stored_entries,
        "prompt_tokens": stats.prompt_tokens,
        "completion_tokens": stats.completion_tokens,
        "total_tokens": stats.total_tokens,
        "saved_tokens": stats.saved_tokens,
    }


@app.post("/v1/embeddings", dependencies=[Depends(require_gateway_token)])
async def create_embeddings(
    payload: EmbeddingRequest,
    cache: Annotated[SemanticCache, Depends(get_semantic_cache)],
) -> EmbeddingResponse:
    """算向量。供雷达判断「两条标题是不是在讲同一件事」。

    为什么放在网关而不是雷达里：模型只有这一份 ONNX 会话，且雷达容器的
    内存上限只有 256m，装不下。让它走这里，既省一份内存，
    也保证两边对同一段文本算出的是同一个向量。

    为什么需要令牌：它是算力接口，敞开等于把 CPU 借出去。
    """
    service = cache.embedding_service
    encoders = {
        "symmetric": service.encode_symmetric,
        "query": service.encode_query,
        "passage": service.encode_passage,
    }
    encode = encoders[payload.input_type]
    return EmbeddingResponse(
        model=get_settings().embedding_model_path,
        data=[
            EmbeddingVector(index=index, embedding=encode(text))
            for index, text in enumerate(payload.input)
        ],
    )


@app.delete("/cache", dependencies=[Depends(require_gateway_token)])
async def clear_cache(
    cache: Annotated[SemanticCache, Depends(get_semantic_cache)],
) -> dict[str, str]:
    cache.clear()
    return {"status": "cleared"}


def _cache_question(request: ChatCompletionRequest) -> str:
    return "\n".join(f"{message.role}: {message.content}" for message in request.messages)


def _answer_from_completion(completion: dict) -> str:
    choices = completion.get("choices", [])
    if not choices:
        raise ValueError("upstream response does not contain choices")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content:
        raise ValueError("upstream response does not contain assistant content")
    return content


@app.post("/v1/chat/completions", dependencies=[Depends(require_gateway_token)])
async def create_chat_completion(
    request: ChatCompletionRequest,
    response: Response,
    client: Annotated[LLMClient, Depends(get_llm_client)],
    cache: Annotated[SemanticCache, Depends(get_semantic_cache)],
    x_cache_mode: Annotated[str, Header()] = "semantic",
) -> dict:
    """Serve a cached answer or forward the request to the configured upstream.

    X-Cache-Mode 头控制复用策略：
      semantic（默认）—— 按向量相似度复用，适合语义不同则问题不同的自然提问。
      exact            —— 仅当文本完全相同时复用。提示词模板占比很高、
                          只有小段内容在变的批量任务（如雷达采集）必须用这个，
                          否则整批请求会共用同一个答案。
    """
    upstream_request = request.model_dump(exclude_none=True)
    cache_mode = "exact" if x_cache_mode.strip().lower() == "exact" else "semantic"

    async def generate_answer() -> GeneratedAnswer:
        completion = await client.chat_completion(upstream_request)
        usage = completion.get("usage") or {}
        return GeneratedAnswer(answer=_answer_from_completion(completion), usage=usage)

    result = await cache.get_or_create(
        question=_cache_question(request),
        generate_answer=generate_answer,
        metadata={"model": request.model or get_settings().llm_model},
        cache_mode=cache_mode,
    )
    response.headers["X-Cache"] = "HIT" if result.hit else "MISS"
    if result.similarity is not None:
        response.headers["X-Cache-Similarity"] = f"{result.similarity:.4f}"

    return {
        "id": f"cache-{result.cache_id or 'generated'}",
        "object": "chat.completion",
        "model": request.model or get_settings().llm_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.answer},
                "finish_reason": "stop",
            }
        ],
        "usage": result.usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
