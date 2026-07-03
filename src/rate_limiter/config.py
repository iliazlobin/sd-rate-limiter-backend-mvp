"""pydantic-settings configuration — all env-driven with safe defaults."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RATELIMIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Rate limiting defaults
    default_limit: int = 100
    default_window_sec: int = 60
    default_burst: int = 100
    lock_cleanup_interval_sec: int = 60
    bucket_idle_ttl_sec: int = 300  # 5 min — cleanup locks for idle buckets


settings = Settings()
