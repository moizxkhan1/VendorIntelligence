from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration. Reads .env on import."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM (provider-agnostic adapter — see app/llm/)
    llm_provider: str = "openai"
    llm_model: str = "gpt-5-mini"
    llm_api_key: str = ""
    llm_base_url: str | None = None

    # Storage
    db_path: str = "./data/vendors.db"

    # Observability
    log_level: str = "INFO"


settings = Settings()
