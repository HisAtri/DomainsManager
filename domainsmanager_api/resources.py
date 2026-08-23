from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from domainsmanager_lookup import DomainLookup
from domainsmanager_application.domains import DomainService
from domainsmanager_application.tasks import RefreshTaskService
from domainsmanager_application.security import (
    AccessTokenService,
    PasswordService,
    RefreshTokenService,
)
from domainsmanager_application.services import AuthConfiguration, AuthService
from domainsmanager_persistence import (
    SqlAlchemyLookupStore,
    create_engine,
    create_session_factory,
)
from domainsmanager_persistence.auth import SqlAlchemyUnitOfWorkFactory

from domainsmanager_api.settings import Settings


@dataclass(slots=True)
class Resources:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    lookup: DomainLookup
    auth: AuthService
    domains: DomainService
    tasks: RefreshTaskService

    async def database_ready(self) -> bool:
        try:
            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            heads = set(ScriptDirectory.from_config(config).get_heads())
            if len(heads) != 1:
                return False
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                revisions = (
                    await connection.execute(text("SELECT version_num FROM alembic_version"))
                ).scalars().all()
        except Exception:
            return False
        return set(revisions) == heads

    async def close(self) -> None:
        await self.engine.dispose()


def create_resources(settings: Settings) -> Resources:
    if settings.jwt_secret_key is None or not settings.jwt_secret_key.get_secret_value():
        raise ValueError("DOMAINSMANAGER_JWT_SECRET_KEY must not be empty")
    if (
        settings.refresh_token_pepper is None
        or not settings.refresh_token_pepper.get_secret_value()
    ):
        raise ValueError("DOMAINSMANAGER_REFRESH_TOKEN_PEPPER must not be empty")

    database = settings.database_config()
    engine = create_engine(database)
    sessions = create_session_factory(engine)
    store = SqlAlchemyLookupStore(sessions)
    lookup = DomainLookup(store=store)
    unit_of_work = SqlAlchemyUnitOfWorkFactory(sessions)
    auth = AuthService(
        unit_of_work=unit_of_work,
        passwords=PasswordService(),
        access_tokens=AccessTokenService(
            secret=settings.jwt_secret_key.get_secret_value(),
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            ttl=timedelta(seconds=settings.access_token_ttl_seconds),
            clock_skew=timedelta(seconds=settings.jwt_clock_skew_seconds),
        ),
        refresh_tokens=RefreshTokenService(
            settings.refresh_token_pepper.get_secret_value()
        ),
        configuration=AuthConfiguration(
            registration_enabled=settings.registration_enabled,
            access_ttl=timedelta(seconds=settings.access_token_ttl_seconds),
            refresh_ttl=timedelta(seconds=settings.refresh_token_ttl_seconds),
        ),
    )
    return Resources(
        engine=engine,
        sessions=sessions,
        lookup=lookup,
        auth=auth,
        domains=DomainService(unit_of_work=unit_of_work, lookup=lookup),
        tasks=RefreshTaskService(unit_of_work=unit_of_work, lookup=lookup),
    )
