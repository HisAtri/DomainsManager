import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SqlAlchemyTimeoutError

from domainsmanager_persistence.db import create_engine
from tests.postgres import assert_dedicated_postgres, postgres_database


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
async def test_postgres_pool_timeout_and_recovery() -> None:
    base = postgres_database()
    await assert_dedicated_postgres(base)
    config = base.model_copy(
        update={"pool_size": 1, "max_overflow": 0, "pool_timeout": 0.05}
    )
    engine = create_engine(config)
    try:
        async with engine.connect():
            with pytest.raises(SqlAlchemyTimeoutError):
                await engine.connect()
        async with engine.connect() as recovered:
            assert await recovered.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
async def test_postgres_command_timeout_allows_new_connection() -> None:
    base = postgres_database()
    await assert_dedicated_postgres(base)
    timeout_config = base.model_copy(update={"command_timeout": 0.05})
    engine = create_engine(timeout_config)
    try:
        with pytest.raises((DBAPIError, TimeoutError)):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT pg_sleep(0.2)"))
    finally:
        await engine.dispose()

    recovery_engine = create_engine(base)
    try:
        async with recovery_engine.connect() as recovered:
            assert await recovered.scalar(text("SELECT 1")) == 1
    finally:
        await recovery_engine.dispose()
