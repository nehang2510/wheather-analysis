from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every field can be overridden with a SKYWATCH_* environment variable."""

    model_config = SettingsConfigDict(env_prefix="SKYWATCH_", env_file=".env", extra="ignore")

    app_name: str = "skywatch"
    debug: bool = False
    log_level: str = "INFO"

    # Upstream
    openmeteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    openmeteo_geocode_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    upstream_timeout_s: float = 12.0
    upstream_retries: int = 3
    past_days: int = 7
    forecast_days: int = 3

    # Caching (seconds)
    forecast_ttl_s: int = 300
    board_ttl_s: int = 600
    geocode_ttl_s: int = 86_400
    cache_max_entries: int = 2_000

    # Rate limiting, per client IP
    rate_limit_per_minute: int = 60
    rate_limit_burst: int = 20
    trust_proxy_headers: bool = True  # Cloud Run / Firebase put the real IP in X-Forwarded-For

    # CORS. Empty list means same-origin only; "*" allows the static file to be opened from file://
    cors_origins: list[str] = Field(default_factory=list)

    static_dir: str = "static"


@lru_cache
def get_settings() -> Settings:
    return Settings()
