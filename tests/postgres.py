from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from domainsmanager_persistence.database_config import DatabaseConfig
from domainsmanager_persistence.db import (
    create_engine,
    downgrade_migrations,
)

PROJECT_TABLES = {
    "alembic_version",
    "app_user",
    "auth_refresh_token",
    "auth_session",
    "domain_check",
    "domain_refresh_task",
    "idempotency_record",
    "lookup_cache_head",
    "lookup_record",
    "lookup_refresh_lease",
    "managed_domain",
    "notification_outbox",
    "notification_rule",
    "parsed_snapshot",
    "public_suffix_rule",
    "security_audit_event",
    "system_state",
}


def postgres_database() -> DatabaseConfig:
    if os.environ.get("DOMAINSMANAGER_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL tests require DOMAINSMANAGER_RUN_POSTGRES_TESTS=1")
    return DatabaseConfig.from_environment(prefix="DOMAINSMANAGER_TEST_DATABASE_")


async def public_tables(config: DatabaseConfig) -> set[str]:
    engine = create_engine(config)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT tablename FROM pg_catalog.pg_tables "
                    "WHERE schemaname = 'public'"
                )
            )
            return set(result.scalars())
    finally:
        await engine.dispose()


async def assert_dedicated_postgres(config: DatabaseConfig) -> None:
    connection_config = config.build_connection()
    if connection_config.url.drivername != "postgresql+asyncpg":
        pytest.fail("PostgreSQL tests require a postgresql+asyncpg configuration")
    engine = create_engine(config)
    try:
        async with engine.connect() as connection:
            metadata = (
                await connection.execute(
                    text(
                        "SELECT current_database() AS database_name, "
                        "current_schema() AS schema_name, "
                        "current_setting('server_version_num')::integer "
                        "AS server_version_num"
                    )
                )
            ).one()
            if metadata.schema_name != "public":
                pytest.fail("PostgreSQL tests require the public schema")
            if metadata.server_version_num < 150000:
                pytest.fail("PostgreSQL 15 or newer is required")
    finally:
        await engine.dispose()


async def clean_project_schema(config: DatabaseConfig) -> None:
    tables = await public_tables(config)
    unknown = tables - PROJECT_TABLES
    if unknown:
        pytest.fail(f"public schema contains unknown tables: {sorted(unknown)}")
    if tables:
        await downgrade_migrations(config)
    remaining = await public_tables(config)
    if remaining == {"alembic_version"}:
        engine = create_engine(config)
        try:
            async with engine.begin() as connection:
                revisions = await connection.scalar(
                    text("SELECT count(*) FROM alembic_version")
                )
                if revisions != 0:
                    pytest.fail("alembic_version still contains a revision")
                await connection.execute(text("DROP TABLE alembic_version"))
        finally:
            await engine.dispose()
        remaining = await public_tables(config)
    project_remaining = remaining & PROJECT_TABLES
    if project_remaining:
        pytest.fail(
            "PostgreSQL cleanup left project tables: "
            f"{sorted(project_remaining)}"
        )
