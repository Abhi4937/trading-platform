from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Delta Exchange — set in .env
    DELTA_API_KEY: str = ""
    DELTA_API_SECRET: str = ""
    DELTA_BASE_URL: str = "https://api.delta.exchange"

    # Redis caching
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL_PRODUCTS: int = 300    # products list (rarely changes)
    CACHE_TTL_EXPIRIES: int = 300    # expiry list (stable)
    CACHE_TTL_SPOT: int = 3          # spot price (deduplicates concurrent calls only)
    CACHE_TTL_CANDLES: int = 60      # OHLCV candles

    RATE_LIMIT_PER_MINUTE: int = 60
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173"]
    RISK_FREE_RATE: float = 0.0      # annualised (crypto has no risk-free rate)

    ENV: str = "development"
    LOG_LEVEL: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
