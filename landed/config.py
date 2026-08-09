"""Runtime configuration, read once from the environment.

PostgreSQL is the only supported database. Supporting a second engine would mean
testing against something other than what ships, and would rule out the features the
schema actually depends on — JSONB with GIN indexes for conflict queries, and CITEXT
so an email is the same address whatever its capitalisation.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_prefix="LANDED_", extra="ignore"
    )

    # --- Persistence ---
    database_url: str = "postgresql+psycopg://landed:landed@localhost:5432/landed"

    # --- Extraction provider ---
    # anthropic | gemini | groq | openai | ollama
    provider: str = "anthropic"
    extraction_model: str = "claude-sonnet-5"
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    # Any OpenAI-compatible endpoint: OpenRouter, Together, DeepSeek, a local vLLM.
    # Left blank, each provider uses its own default.
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    ollama_host: str = "http://localhost:11434"

    # --- Web ---
    secret_key: str = "dev-only-change-me"
    session_max_age_seconds: int = 60 * 60 * 12
    # Set true wherever the app is served over TLS, which marks the session cookie
    # Secure. Off by default because the local demo runs over plain http, and a
    # Secure cookie there is silently never sent — an unexplained logged-out app.
    secure_cookies: bool = False

    # --- Storage ---
    upload_dir: Path = REPO_ROOT / "uploads"

    # --- Reproducibility ---
    seed: int = 42


    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, url: str) -> str:
        """Normalise the scheme a managed host hands out.

        Render, Heroku and most others publish `postgres://` or `postgresql://`.
        SQLAlchemy resolves both to psycopg2, which is not what this project installs
        — the result is `ModuleNotFoundError: psycopg2` at first connection, on the
        deployed box only. Rewriting the scheme here means the platform's own
        connection string can be pasted in unedited.
        """
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Cached so configuration is read once per process."""
    return Settings()
