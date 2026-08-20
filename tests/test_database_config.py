from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from domainsmanager_persistence.database_config import (
    DatabaseConfig,
    DatabaseSSLMode,
    DatabaseType,
)
from domainsmanager_persistence.db import create_alembic_config


@pytest.mark.unit
def test_builds_postgresql_connection_from_split_fields() -> None:
    config = DatabaseConfig(
        type="postgresql",
        host="db.example",
        port=5544,
        name="domains",
        user="app-user",
        password="secret",
        pool_size=3,
        max_overflow=4,
        pool_timeout=5,
        pool_recycle=600,
        connect_timeout=2,
        command_timeout=9,
    )

    connection = config.build_connection()

    assert connection.url.drivername == "postgresql+asyncpg"
    assert connection.url.host == "db.example"
    assert connection.url.port == 5544
    assert connection.url.database == "domains"
    assert connection.url.username == "app-user"
    assert connection.url.password == "secret"
    assert connection.connect_args == {
        "timeout": 2.0,
        "command_timeout": 9.0,
        "ssl": False,
    }
    assert connection.engine_options == {
        "pool_pre_ping": True,
        "pool_size": 3,
        "max_overflow": 4,
        "pool_timeout": 5.0,
        "pool_recycle": 600,
    }


@pytest.mark.unit
def test_structured_url_handles_special_character_credentials() -> None:
    password = "p@ss:/?#% space密码"
    config = DatabaseConfig(
        type="postgresql",
        host="localhost",
        name="db/name",
        user="user@tenant",
        password=password,
    )

    url = config.build_connection().url
    rendered = url.render_as_string(hide_password=False)
    parsed = make_url(rendered)

    assert parsed.username == "user@tenant"
    assert parsed.password == password
    assert parsed.database == "db/name"
    assert password not in str(url)
    assert "***" in str(url)


@pytest.mark.unit
@pytest.mark.parametrize("path", [":memory:", "./local.db"])
def test_builds_sqlite_connection_from_path(path: str) -> None:
    config = DatabaseConfig(type="sqlite", path=path)

    connection = config.build_connection()

    assert connection.url.drivername == "sqlite+aiosqlite"
    expected = path if path == ":memory:" else str(Path(path))
    assert connection.url.database == expected
    assert connection.connect_args == {}
    assert connection.engine_options == {}


@pytest.mark.unit
def test_verify_ssl_requires_ca_file() -> None:
    with pytest.raises(ValidationError, match="SSL CA is required"):
        DatabaseConfig(
            type="postgresql",
            host="localhost",
            name="domains",
            user="app",
            ssl_mode="verify-full",
        )


@pytest.mark.unit
def test_verify_full_ssl_context_checks_hostname(tmp_path: Path) -> None:
    # An empty file is not a valid CA bundle; validation is deferred to SSL creation.
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("", encoding="ascii")
    config = DatabaseConfig(
        type="postgresql",
        host="localhost",
        name="domains",
        user="app",
        ssl_mode=DatabaseSSLMode.VERIFY_FULL,
        ssl_ca=ca_file,
    )

    with pytest.raises(Exception):
        config.build_connection()


@pytest.mark.unit
@pytest.mark.parametrize(
    "fields",
    [
        {"type": "postgresql", "host": "", "name": "db", "user": "user"},
        {"type": "postgresql", "host": "db", "name": "", "user": "user"},
        {"type": "postgresql", "host": "db", "name": "db", "user": ""},
        {"type": "sqlite", "path": None},
        {"type": "sqlite", "path": ":memory:", "ssl_mode": "require"},
    ],
)
def test_rejects_incomplete_or_incompatible_configuration(fields: dict) -> None:
    with pytest.raises(ValidationError):
        DatabaseConfig.model_validate(fields)


@pytest.mark.unit
def test_loads_database_configuration_from_environment() -> None:
    config = DatabaseConfig.from_environment(
        environment={
            "DOMAINSMANAGER_DATABASE_TYPE": "postgresql",
            "DOMAINSMANAGER_DATABASE_HOST": "172.19.174.204",
            "DOMAINSMANAGER_DATABASE_PORT": "5432",
            "DOMAINSMANAGER_DATABASE_NAME": "postgres",
            "DOMAINSMANAGER_DATABASE_USER": "postgres",
            "DOMAINSMANAGER_DATABASE_PASSWORD": "runtime-secret",
            "DOMAINSMANAGER_DATABASE_POOL_SIZE": "2",
        }
    )

    assert config.type is DatabaseType.POSTGRESQL
    assert config.host == "172.19.174.204"
    assert config.name == "postgres"
    assert config.user == "postgres"
    assert config.password is not None
    assert config.password.get_secret_value() == "runtime-secret"
    assert config.pool_size == 2
    assert "runtime-secret" not in repr(config)


@pytest.mark.unit
def test_alembic_configuration_preserves_percent_encoded_credentials() -> None:
    connection = DatabaseConfig(
        type="postgresql",
        host="localhost",
        name="domains",
        user="app",
        password="percent%password",
    ).build_connection()

    alembic = create_alembic_config(connection)
    rendered = alembic.get_main_option("sqlalchemy.url")

    assert make_url(rendered).password == "percent%password"
