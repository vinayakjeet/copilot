from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"

    # The app connects only through the read-only role. provision.py is the one
    # thing allowed to hold the owner URL, and it refuses to run against the
    # read-only DSN by accident because the role it creates would not exist yet.
    copilot_ro_url: str = ""
    copilot_db_url: str = ""

    copilot_llm_provider: str = "mock"
    tollgate_url: str = "http://127.0.0.1:8077/v1"
    copilot_model: str = "groq/llama-3.3-70b-versatile"

    statement_timeout_ms: int = 5_000
    row_cap: int = 500
    max_correction_attempts: int = 1

    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_headers: str = ""

    @property
    def llm_max_retry_attempts(self) -> int:
        return 3


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Test seam: settings are cached process-wide, so tests that rewrite env
    need a way to drop the cache rather than import-order luck."""
    global _settings
    _settings = None
