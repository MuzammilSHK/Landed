"""Runtime configuration, read once from the environment.

PostgreSQL is the deployment target; SQLite is kept working so a reviewer can clone
the repository and run the app without standing up a server first. Every model in
`landed.db` uses portable column types so the two stay interchangeable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_prefix="LANDED_", extra="ignore"
    )

    # --- Persistence ---
    database_url: str = "sqlite:///./landed.db"

    # --- Extraction provider ---
    provider: str = "anthropic"                    # anthropic | gemini | ollama
    extraction_model: str = "claude-sonnet-5"
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    ollama_host: str = "http://localhost:11434"

    # --- Web ---
    secret_key: str = "dev-only-change-me"
    session_max_age_seconds: int = 60 * 60 * 12

    # --- Storage ---
    upload_dir: Path = REPO_ROOT / "uploads"

    # --- Reproducibility ---
    seed: int = 42

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Cached so configuration is read once per process."""
    return Settings()
