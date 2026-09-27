from typing import Any

from openai import AsyncOpenAI

from gateway.app.config import Settings


class LLMClient:
    """Thin adapter around an OpenAI-compatible upstream model service."""

    def __init__(self, settings: Settings) -> None:
        self._model = settings.llm_model
        self._client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )

    async def chat_completion(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request)
        payload["model"] = payload.get("model") or self._model

        completion = await self._client.chat.completions.create(**payload)
        return completion.model_dump()
