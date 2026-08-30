from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from domainsmanager_persistence.database_config import (
    DatabaseConfig,
    DatabaseSSLMode,
    DatabaseType,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DOMAINSMANAGER_",
        extra="ignore",
    )

    app_name: str = "DomainsManager"
    app_version: str = "0.1.0"
    database_type: DatabaseType = DatabaseType.SQLITE
    database_host: str | None = None
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str | None = None
    database_user: str | None = None
    database_password: SecretStr | None = None
    database_path: str | None = "domainsmanager.db"
    database_ssl_mode: DatabaseSSLMode = DatabaseSSLMode.DISABLE
    database_ssl_ca: Path | None = None
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_timeout: float = Field(default=30.0, gt=0)
    database_pool_recycle: int = Field(default=1800, ge=-1)
    database_connect_timeout: float = Field(default=10.0, gt=0)
    database_command_timeout: float = Field(default=30.0, gt=0)
    migrate_on_startup: bool = False
    server_host: str = "127.0.0.1"
    server_port: int = Field(default=7920, ge=1, le=65535)
    registration_enabled: bool = False
    jwt_secret_key: SecretStr | None = None
    refresh_token_pepper: SecretStr | None = None
    jwt_issuer: str = "domainsmanager"
    jwt_audience: str = "domainsmanager-api"
    access_token_ttl_seconds: int = Field(default=900, ge=60)
    refresh_token_ttl_seconds: int = Field(default=2_592_000, ge=60)
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    rate_limit_redis_url: str | None = None
    rate_limit_redis_key_prefix: str = Field(
        default="domainsmanager:rate-limit", min_length=1, max_length=128
    )
    normal_rate_limit_attempts: int = Field(default=300, ge=1, le=10_000)
    normal_rate_limit_window_seconds: int = Field(default=120, ge=1, le=86_400)
    expensive_rate_limit_attempts: int = Field(default=30, ge=1, le=10_000)
    expensive_rate_limit_window_seconds: int = Field(default=120, ge=1, le=86_400)
    refresh_cookie_name: str = Field(
        default="domainsmanager_refresh", min_length=1, max_length=128
    )
    # Set this to true in HTTPS deployments. It remains false by default so the
    # bundled HTTP development server can authenticate through Vite's proxy.
    refresh_cookie_secure: bool = False
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    jwt_clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    task_lease_seconds: int = Field(default=120, ge=1)
    task_max_attempts: int = Field(default=5, ge=1)
    task_retry_base_seconds: int = Field(default=60, ge=0)
    task_retry_max_seconds: int = Field(default=3600, ge=0)
    check_interval_seconds: int = Field(default=86_400, ge=1)
    successful_refresh_ttl_seconds: int = Field(default=1_800, ge=0)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    scheduler_poll_interval_seconds: float = Field(default=10.0, gt=0)
    scheduler_batch_size: int = Field(default=100, ge=1)
    notification_delivery_timeout_seconds: float = Field(default=10, gt=0)
    notification_worker_poll_interval_seconds: float = Field(default=1, gt=0)
    notification_max_attempts: int = Field(default=5, ge=1)
    notification_retry_base_seconds: int = Field(default=60, ge=0)
    notification_retry_max_seconds: int = Field(default=3600, ge=0)
    email_verification_enabled: bool = False
    email_domain_allowlist: str = ""
    webhook_proxy_url: str | None = None
    smtp_enabled: bool = True
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_from: str | None = None
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_encryption: Literal["none", "starttls", "ssl_tls"] = "starttls"
    smtp_starttls: bool = True
    configuration_encryption_key: SecretStr | None = None
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: SecretStr | None = None
    request_id_header: str = "X-Request-ID"
    docs_enabled: bool = True
    cors_origins: list[str] = Field(default_factory=list)
    oauth_providers: list[str] = Field(default_factory=list)
    api_prefix: str = Field(default="/api/v1", pattern=r"^/[a-z0-9/_-]+$")

    def database_config(self) -> DatabaseConfig:
        return DatabaseConfig(
            type=self.database_type,
            host=self.database_host,
            port=self.database_port,
            name=self.database_name,
            user=self.database_user,
            password=self.database_password,
            path=(
                self.database_path
                if self.database_type is DatabaseType.SQLITE
                else None
            ),
            ssl_mode=self.database_ssl_mode,
            ssl_ca=self.database_ssl_ca,
            pool_size=self.database_pool_size,
            max_overflow=self.database_max_overflow,
            pool_timeout=self.database_pool_timeout,
            pool_recycle=self.database_pool_recycle,
            connect_timeout=self.database_connect_timeout,
            command_timeout=self.database_command_timeout,
        )

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if "smtp_encryption" not in self.model_fields_set and "smtp_starttls" in self.model_fields_set:
            self.smtp_encryption = "starttls" if self.smtp_starttls else "none"
        self.smtp_starttls = self.smtp_encryption == "starttls"
        username_set = self.bootstrap_admin_username is not None
        password_set = self.bootstrap_admin_password is not None
        if username_set != password_set:
            raise ValueError(
                "bootstrap admin username and password must be configured together"
            )
        if "*" in self.cors_origins:
            raise ValueError("cors_origins cannot include '*' when credentials are enabled")
        if self.task_retry_max_seconds < self.task_retry_base_seconds:
            raise ValueError(
                "task retry max seconds must not be less than the base delay"
            )
        if self.notification_retry_max_seconds < self.notification_retry_base_seconds:
            raise ValueError(
                "notification retry max seconds must not be less than the base delay"
            )
        if self.webhook_proxy_url:
            proxy = urlsplit(self.webhook_proxy_url)
            if proxy.scheme not in {"http", "socks5"} or not proxy.hostname:
                raise ValueError("webhook_proxy_url must be an HTTP or SOCKS5 URL")
            if proxy.fragment:
                raise ValueError("webhook_proxy_url must not contain a fragment")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
