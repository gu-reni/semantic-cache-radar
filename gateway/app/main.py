from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Response

from gateway.app.cache_service import SemanticCache
from gateway.app.config import get_settings
from gateway.app.embeddings import EmbeddingService
from gateway.app.llm_client import LLMClient
from gateway.app.schemas import ChatCompletionRequest
from gateway.app.vector_store import VectorStore

app = FastAPI(
    title="Semantic Cache Gateway",
    version="0.1.0",
)


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


@app.post("/v1/chat/completions")
async def create_chat_completion(
    request: ChatCompletionRequest,
    response: Response,
    client: Annotated[LLMClient, Depends(get_llm_client)],
    cache: Annotated[SemanticCache, Depends(get_semantic_cache)],
) -> dict:
    """Serve a cached answer or forward the request to the configured upstream."""
    upstream_request = request.model_dump(exclude_none=True)

    async def generate_answer() -> str:
        completion = await client.chat_completion(upstream_request)
        return _answer_from_completion(completion)

    result = await cache.get_or_create(
        question=_cache_question(request),
        generate_answer=generate_answer,
        metadata={"model": request.model or get_settings().llm_model},
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
        "usage": {
            "prompt_tokens": 0 if result.hit else None,
            "completion_tokens": 0 if result.hit else None,
            "total_tokens": 0 if result.hit else None,
        },
    }
