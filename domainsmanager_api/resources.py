from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from domainsmanager_api.global_setting_registry import GLOBAL_SETTING_BY_KEY
from domainsmanager_api.notifier import deliver
from domainsmanager_api.settings import Settings
from domainsmanager_application.domains import DomainService
from domainsmanager_application.notifications import (
    NotificationOutboxService,
    NotificationRuleService,
)
from domainsmanager_application.scheduler import DomainSchedulerService, SchedulerPolicy
from domainsmanager_application.security import (
    AccessTokenService,
    PasswordService,
    RefreshTokenService,
)
from domainsmanager_application.services import AuthConfiguration, AuthService
from domainsmanager_application.tasks import RefreshTaskService, TaskExecutionPolicy
from domainsmanager_lookup import DomainLookup
from domainsmanager_persistence import (
    SqlAlchemyLookupStore,
    create_engine,
    create_session_factory,
)
from domainsmanager_persistence.auth import SqlAlchemyUnitOfWorkFactory
from domainsmanager_persistence.models import GlobalSetting


@dataclass(slots=True)
class Resources:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    lookup: DomainLookup
    auth: AuthService
    domains: DomainService
    tasks: RefreshTaskService
    scheduler: DomainSchedulerService
    notifications: NotificationRuleService
    notifier: NotificationOutboxService

    async def reload_global_policies(self) -> Settings:
        """Apply persisted non-secret settings when a process starts."""
        keys = [key for key, definition in GLOBAL_SETTING_BY_KEY.items() if not definition.secret]
        async with self.sessions() as session:
            rows = {
                row.key: row.value
                for row in (
                    await session.execute(
                        select(GlobalSetting).where(GlobalSetting.key.in_(keys))
                    )
                ).scalars()
            }
        overrides: dict[str, object] = {}
        for key, raw in rows.items():
            definition = GLOBAL_SETTING_BY_KEY[key]
            if definition.kind == "boolean":
                overrides[key] = raw == "true"
            elif definition.kind == "integer":
                overrides[key] = int(raw)
            elif definition.kind == "number":
                overrides[key] = float(raw)
            else:
                overrides[key] = raw or None
        effective = self.settings.model_copy(update=overrides)
        self.auth._configuration = AuthConfiguration(
            registration_enabled=effective.registration_enabled,
            access_ttl=timedelta(seconds=effective.access_token_ttl_seconds),
            refresh_ttl=timedelta(seconds=effective.refresh_token_ttl_seconds),
        )
        self.domains._initial_task_max_attempts = effective.task_max_attempts
        self.tasks._policy = TaskExecutionPolicy(
            lease_duration=timedelta(seconds=effective.task_lease_seconds),
            max_attempts=effective.task_max_attempts,
            retry_base_delay=timedelta(seconds=effective.task_retry_base_seconds),
            retry_max_delay=timedelta(seconds=effective.task_retry_max_seconds),
            successful_check_interval=timedelta(seconds=effective.check_interval_seconds),
            successful_refresh_ttl=timedelta(seconds=effective.successful_refresh_ttl_seconds),
        )
        self.scheduler._policy = SchedulerPolicy(
            check_interval=timedelta(seconds=effective.check_interval_seconds),
            batch_size=effective.scheduler_batch_size,
        )
        self.notifier._max_attempts = effective.notification_max_attempts
        self.notifier._retry_base_delay = timedelta(seconds=effective.notification_retry_base_seconds)
        self.notifier._retry_max_delay = timedelta(seconds=effective.notification_retry_max_seconds)
        return effective

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
        except Exception:  # noqa: BLE001 - readiness must never leak backend failures
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
        settings=settings,
        engine=engine,
        sessions=sessions,
        lookup=lookup,
        auth=auth,
        domains=DomainService(
            unit_of_work=unit_of_work,
            lookup=lookup,
            initial_task_max_attempts=settings.task_max_attempts,
        ),
        tasks=RefreshTaskService(
            unit_of_work=unit_of_work,
            lookup=lookup,
            policy=TaskExecutionPolicy(
                lease_duration=timedelta(seconds=settings.task_lease_seconds),
                max_attempts=settings.task_max_attempts,
                retry_base_delay=timedelta(seconds=settings.task_retry_base_seconds),
                retry_max_delay=timedelta(seconds=settings.task_retry_max_seconds),
                successful_check_interval=timedelta(
                    seconds=settings.check_interval_seconds
                ),
                successful_refresh_ttl=timedelta(
                    seconds=settings.successful_refresh_ttl_seconds
                ),
            ),
        ),
        scheduler=DomainSchedulerService(
            unit_of_work=unit_of_work,
            policy=SchedulerPolicy(
                check_interval=timedelta(seconds=settings.check_interval_seconds),
                batch_size=settings.scheduler_batch_size,
            ),
        ),
        notifications=NotificationRuleService(unit_of_work=unit_of_work),
        notifier=NotificationOutboxService(
            unit_of_work=unit_of_work,
            deliver=lambda message: deliver(message, settings, sessions),
            max_attempts=settings.notification_max_attempts,
            retry_base_delay=timedelta(seconds=settings.notification_retry_base_seconds),
            retry_max_delay=timedelta(seconds=settings.notification_retry_max_seconds),
        ),
    )
