from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://milieu:milieu@localhost:5544/milieu"
    redis_url: str = "redis://localhost:6389/0"

    # "openrouter" or "groq" — see app/core/factories.py. Groq is the fallback: its free
    # tier runs on Groq's own hardware with its own account limits, not OpenRouter's pooled
    # free-tier capacity shared across every user (the actual cause of this project's
    # sustained 429s, not the specific model chosen within OpenRouter).
    llm_provider: str = "openrouter"
    openrouter_api_key: str = ""
    groq_api_key: str = ""
    llm_model: str = "nvidia/nemotron-3.5-lightning:free"
    openrouter_app_name: str = "Milieu"
    openrouter_site_url: str = "https://github.com/milieu"

    embedding_provider: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    slack_bot_token: str = ""
    slack_signing_secret: str = ""

    tenant_id: str = "00000000-0000-0000-0000-000000000001"
    prompt_version: str = "1"
    schema_version: str = "1"

    mock_data_dir: str = str(_REPO_ROOT / "mock-data")

    # CORS allowlist for the dashboard dev server (`web/`, §17, §20). Comma-separated env var.
    dashboard_origins_raw: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="DASHBOARD_ORIGINS"
    )

    @property
    def dashboard_origins(self) -> list[str]:
        return [o.strip() for o in self.dashboard_origins_raw.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
