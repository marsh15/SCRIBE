from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    internal_service_secret: str = Field(default="", alias="INTERNAL_SERVICE_SECRET")
    # Neon pooled URL in production; docker Postgres locally. NEON_DATABASE_URL is
    # accepted so the same Vercel env var serves both runtimes.
    database_url: str = Field(default="", alias="DATABASE_URL")
    neon_database_url: str = Field(default="", alias="NEON_DATABASE_URL")
    token_audience: str = "scribe-intel"

    @property
    def dsn(self) -> str:
        return self.database_url or self.neon_database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
