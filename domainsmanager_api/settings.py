from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DOMAINSMANAGER_",
        extra="ignore",
    )

    app_name: str = "DomainsManager"
    app_version: str = "0.1.0"
    database_url: str = "postgresql+asyncpg://localhost/domainsmanager"
    server_host: str = "127.0.0.1"
    server_port: int = Field(default=7920, ge=1, le=65535)
    registration_enabled: bool = False
    request_id_header: str = "X-Request-ID"
    docs_enabled: bool = True
    api_prefix: str = Field(default="/api/v1", pattern=r"^/[a-z0-9/_-]+$")


@lru_cache
def get_settings() -> Settings:
    return Settings()
