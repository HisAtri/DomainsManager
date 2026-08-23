import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from domainsmanager_persistence.database_config import (
    DatabaseConfig,
    DatabaseConnectionConfig,
)
from domainsmanager_persistence.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_connection() -> DatabaseConnectionConfig:
    configured = config.attributes.get("database_connection_config")
    if configured is not None:
        return configured
    return DatabaseConfig().build_connection()


def run_migrations_offline() -> None:
    connection = database_connection()
    context.configure(
        url=connection.url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connection = database_connection()
    connectable = create_async_engine(
        connection.url,
        connect_args=connection.connect_args,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as migration_connection:
        await migration_connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
