from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./reviews.db"
    groq_api_key: str | None = None
    llm_model: str = "openai/gpt-oss-120b"
    llm_timeout_s: int = 45
    github_token: str | None = None            # lets us fetch private PRs and post review comments
    github_webhook_secret: str | None = None   # enables /webhooks/github
    cors_origins: list[str] = ["http://localhost:5173"]
    max_files: int = 30
    max_file_bytes: int = 120_000
    embedding_backend: str = "fastembed"       # "fastembed" | "hash" (offline, used in tests)


@lru_cache
def get_settings() -> Settings:
    return Settings()
