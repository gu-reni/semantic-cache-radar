from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = None


class EmbeddingRequest(BaseModel):
    """算向量的请求；供雷达做「两条标题是不是在讲同一件事」的判断。

    为什么要显式声明 input_type：e5 系列要求两侧加不同的前缀
    （query: / passage:）。算错前缀不会报错，只会让相似度整体偏低，
    表现为「明明是一回事却判成不相似」—— 属于那种不会崩、只是悄悄变差的错。

    symmetric 是给「两边同质」的比较用的（标题对标题就是这种），
    它把两侧按同一种方式编码，避免把谁当查询、谁当文档的偏心。
    """

    input: list[str] = Field(min_length=1, max_length=64)
    input_type: Literal["symmetric", "query", "passage"] = "symmetric"


class EmbeddingVector(BaseModel):
    index: int
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    model: str
    data: list[EmbeddingVector]
