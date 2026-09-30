from gateway.app.config import Settings


def test_settings_have_safe_defaults() -> None:
    settings=Settings(_env_file=None)

    assert settings.app_env=="development"
    assert settings.gateway_host=="127.0.0.1"
    assert settings.gateway_port==8000
    assert settings.cache_similarity_threshold==0.92
    assert settings.cache_ttl_seconds==86400
    assert settings.llm_api_key==""



def test_settings_accept_environment_calues() -> None:
    settings=Settings(
        _env_file=None,
        LLM_MODEL="test-model",
        CACHE_SIMILARITY_THRESHOLD="0.95",
        CACHE_TTL_SECONDS="3600",
    )

    assert settings.llm_model=="test-model"
    assert settings.cache_similarity_threshold==0.95
    assert settings.cache_ttl_seconds==3600
