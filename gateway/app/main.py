from typing import Annotated

from fastapi import Depends, FastAPI

from gateway.app.config import get_settings
from gateway.app.llm_client import LLMClient
from gateway.app.schemas import ChatCompletionRequest

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


@app.post("/v1/chat/completions")
async def create_chat_completion(
    request: ChatCompletionRequest,
    client: Annotated[LLMClient, Depends(get_llm_client)],
) -> dict:
    """Forward an OpenAI-compatible chat request to the configured upstream."""
    return await client.chat_completion(request.model_dump(exclude_none=True))
