from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, event
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from domainsmanager_persistence.database_config import (
    DatabaseConfig,
    DatabaseConnectionConfig,
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(
    configuration: DatabaseConfig | DatabaseConnectionConfig,
    **overrides,
) -> AsyncEngine:
    connection = (
        configuration.build_connection()
        if isinstance(configuration, DatabaseConfig)
        else configuration
    )
    options = {
        **connection.engine_options,
        **overrides,
    }
    connect_args = {
        **connection.connect_args,
        **options.pop("connect_args", {}),
    }
    engine = create_async_engine(
        connection.url,
        connect_args=connect_args,
        **options,
    )
    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)
    return engine


def _configure_sqlite_connection(dbapi_connection, connection_record) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def run_migrations(
    configuration: DatabaseConfig | DatabaseConnectionConfig,
    revision: str = "head",
) -> None:
    connection = (
        configuration.build_connection()
        if isinstance(configuration, DatabaseConfig)
        else configuration
    )
    config = create_alembic_config(connection)
    await asyncio.to_thread(command.upgrade, config, revision)


async def downgrade_migrations(
    configuration: DatabaseConfig | DatabaseConnectionConfig,
    revision: str = "base",
) -> None:
    connection = (
        configuration.build_connection()
        if isinstance(configuration, DatabaseConfig)
        else configuration
    )
    config = create_alembic_config(connection)
    await asyncio.to_thread(command.downgrade, config, revision)


def create_alembic_config(connection: DatabaseConnectionConfig) -> Config:
    package_root = Path(__file__).parent
    config_path = package_root / "alembic.ini"
    if not config_path.exists():
        config_path = package_root.parent / "alembic.ini"
    config = Config(str(config_path))
    rendered = connection.url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", rendered)
    config.attributes["database_connection_config"] = connection
    return config
