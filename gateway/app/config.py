from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 8000

    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_api_key: str = ""

    embedding_model_path: str = "./models/multilingual-e5-small"
    # ONNX 推理线程数；按 CPU 核数设置，2 核机器上设 2 即可。
    embedding_threads: int = 2

    # 网关共享令牌。为空时不校验，仅限本地开发；
    # 部署到公网机器上必须设置，否则任何人都能借网关的 Key 刷上游 Token。
    gateway_auth_token: str = ""

    vector_store_path: str = "./data/chroma"
    cache_similarity_threshold: float = 0.92
    cache_ttl_seconds: int = 86400
    # 统计计数器的落盘路径；重启后从这份 JSON 恢复，否则命中/未命中计数会归零，
    # 「语义缓存到底有没有被用上」就永远只能靠读代码推理。容器内路径即可。
    stats_path: str = "./data/cache_stats.json"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for the current process."""
    return Settings()
