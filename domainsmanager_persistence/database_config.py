from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from pydantic import ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class DatabaseType(StrEnum):
    POSTGRESQL = "postgresql"
    SQLITE = "sqlite"


class DatabaseSSLMode(StrEnum):
    DISABLE = "disable"
    REQUIRE = "require"
    VERIFY_CA = "verify-ca"
    VERIFY_FULL = "verify-full"


@dataclass(frozen=True, slots=True)
class DatabaseConnectionConfig:
    url: URL
    connect_args: dict[str, object]
    engine_options: dict[str, object]


class DatabaseConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DOMAINSMANAGER_DATABASE_",
        extra="ignore",
    )

    type: DatabaseType = DatabaseType.SQLITE
    host: str | None = None
    port: int = Field(default=5432, ge=1, le=65535)
    name: str | None = None
    user: str | None = None
    password: SecretStr | None = None
    path: str | None = None
    ssl_mode: DatabaseSSLMode = DatabaseSSLMode.DISABLE
    ssl_ca: Path | None = None
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    pool_timeout: float = Field(default=30.0, gt=0)
    pool_recycle: int = Field(default=1800, ge=-1)
    connect_timeout: float = Field(default=10.0, gt=0)
    command_timeout: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def validate_database_fields(self) -> "DatabaseConfig":
        if self.type is DatabaseType.POSTGRESQL:
            missing = [
                field
                for field, value in (
                    ("host", self.host),
                    ("name", self.name),
                    ("user", self.user),
                )
                if value is None or not value.strip()
            ]
            if missing:
                raise ValueError(
                    "PostgreSQL configuration requires: " + ", ".join(missing)
                )
            if self.path is not None:
                raise ValueError("database path is only valid for SQLite")
            if self.ssl_mode in {
                DatabaseSSLMode.VERIFY_CA,
                DatabaseSSLMode.VERIFY_FULL,
            } and self.ssl_ca is None:
                raise ValueError("database SSL CA is required for certificate verification")
        else:
            if self.path is None or not self.path.strip():
                raise ValueError("SQLite configuration requires a database path")
            if self.ssl_mode is not DatabaseSSLMode.DISABLE or self.ssl_ca is not None:
                raise ValueError("database SSL options are not valid for SQLite")
        return self

    def build_connection(self) -> DatabaseConnectionConfig:
        if self.type is DatabaseType.SQLITE:
            return self._build_sqlite_connection()
        return self._build_postgresql_connection()

    def _build_postgresql_connection(self) -> DatabaseConnectionConfig:
        assert self.host is not None
        assert self.name is not None
        assert self.user is not None
        password = (
            self.password.get_secret_value() if self.password is not None else None
        )
        connect_args: dict[str, object] = {
            "timeout": self.connect_timeout,
            "command_timeout": self.command_timeout,
            "ssl": self._build_ssl_argument(),
        }
        return DatabaseConnectionConfig(
            url=URL.create(
                "postgresql+asyncpg",
                username=self.user,
                password=password,
                host=self.host,
                port=self.port,
                database=self.name,
            ),
            connect_args=connect_args,
            engine_options={
                "pool_pre_ping": True,
                "pool_size": self.pool_size,
                "max_overflow": self.max_overflow,
                "pool_timeout": self.pool_timeout,
                "pool_recycle": self.pool_recycle,
            },
        )

    def _build_sqlite_connection(self) -> DatabaseConnectionConfig:
        assert self.path is not None
        database = self.path
        if database != ":memory:":
            database = str(Path(database).expanduser())
        return DatabaseConnectionConfig(
            url=URL.create("sqlite+aiosqlite", database=database),
            connect_args={},
            engine_options={},
        )

    def _build_ssl_argument(self) -> bool | ssl.SSLContext:
        if self.ssl_mode is DatabaseSSLMode.DISABLE:
            return False
        if self.ssl_mode is DatabaseSSLMode.REQUIRE:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context
        assert self.ssl_ca is not None
        context = ssl.create_default_context(cafile=str(self.ssl_ca))
        context.check_hostname = self.ssl_mode is DatabaseSSLMode.VERIFY_FULL
        return context

    @classmethod
    def from_environment(
        cls,
        prefix: str = "DOMAINSMANAGER_DATABASE_",
        environment: Mapping[str, str] | None = None,
    ) -> "DatabaseConfig":
        values = environment if environment is not None else os.environ
        fields: dict[str, object] = {}
        for field_name in cls.model_fields:
            key = f"{prefix}{field_name.upper()}"
            if key in values:
                fields[field_name] = values[key]
        return cls.model_validate(fields)
