from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from domainsmanager_lookup import DomainLookup
from domainsmanager_persistence import (
    SqlAlchemyLookupStore,
    create_engine,
    create_session_factory,
)

from domainsmanager_api.settings import Settings


@dataclass(slots=True)
class Resources:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    lookup: DomainLookup

    async def database_ready(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    async def close(self) -> None:
        await self.engine.dispose()


def create_resources(settings: Settings) -> Resources:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    sessions = create_session_factory(engine)
    store = SqlAlchemyLookupStore(sessions)
    return Resources(
        engine=engine,
        sessions=sessions,
        lookup=DomainLookup(store=store),
    )
