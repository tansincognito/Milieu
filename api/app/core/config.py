from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://milieu:milieu@localhost:5544/milieu"
    redis_url: str = "redis://localhost:6389/0"

    llm_provider: str = "openrouter"
    openrouter_api_key: str = ""
    llm_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    openrouter_app_name: str = "Milieu"
    openrouter_site_url: str = "https://github.com/milieu"

    embedding_provider: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    slack_bot_token: str = ""
    slack_signing_secret: str = ""

    tenant_id: str = "00000000-0000-0000-0000-000000000001"
    prompt_version: str = "1"
    schema_version: str = "1"

    mock_data_dir: str = "../mock-data"


@lru_cache
def get_settings() -> Settings:
    return Settings()
