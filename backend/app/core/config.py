"""Central settings (12-factor, .env supported)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: str = "development"
    DATABASE_URL: str = "sqlite:///./lorebound.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = "dev-secret-change-me-min-32-chars-xxxx"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7
    AI_PROVIDER: str = "stub"
    AI_MODEL: str = "stub-narrator-1"
    # Engine pilot (phase 2; plan §1): off by default — every /engine/* path
    # 404s until a deploy sets ENGINE_MODE=1. Campaign files live in
    # ENGINE_DATA_DIR (deploys mount a volume there).
    ENGINE_MODE: bool = False
    ENGINE_DATA_DIR: str = "./engine_data"


settings = Settings()
