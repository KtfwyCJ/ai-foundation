from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the platform."""

    model_config = SettingsConfigDict(env_prefix="AI_PLATFORM_", env_file=".env")

    # Auth: API keys allowed to call the gateway. Comma-separated in env.
    api_keys: str = "dev-local-key"

    # Rate limiting: requests allowed per key per window.
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    @property
    def api_key_set(self) -> set[str]:
        return {key.strip() for key in self.api_keys.split(",") if key.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
