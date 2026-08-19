from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
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
    jwt_secret_key: SecretStr | None = None
    refresh_token_pepper: SecretStr | None = None
    jwt_issuer: str = "domainsmanager"
    jwt_audience: str = "domainsmanager-api"
    access_token_ttl_seconds: int = Field(default=900, ge=60)
    refresh_token_ttl_seconds: int = Field(default=2_592_000, ge=60)
    jwt_clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: SecretStr | None = None
    request_id_header: str = "X-Request-ID"
    docs_enabled: bool = True
    api_prefix: str = Field(default="/api/v1", pattern=r"^/[a-z0-9/_-]+$")

    @model_validator(mode="after")
    def validate_bootstrap_admin(self) -> "Settings":
        username_set = self.bootstrap_admin_username is not None
        password_set = self.bootstrap_admin_password is not None
        if username_set != password_set:
            raise ValueError(
                "bootstrap admin username and password must be configured together"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
