import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from domainsmanager_persistence.database_config import DatabaseConfig
from domainsmanager_persistence.db import create_engine
from tests.postgres import postgres_database


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
async def test_postgresql_pool_timeout_recovery_and_dispose() -> None:
    base = postgres_database()
    values = base.model_dump()
    values.update(pool_size=1, max_overflow=0, pool_timeout=0.2)
    config = DatabaseConfig.model_validate(values)
    engine = create_engine(config)
    first = await engine.connect()
    try:
        with pytest.raises(SQLAlchemyTimeoutError):
            await engine.connect()
    finally:
        await first.close()

    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1
    await engine.dispose()

    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1
    await engine.dispose()
