from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from domainsmanager_api.api.auth import router as auth_router
from domainsmanager_api.api.admin import router as admin_router
from domainsmanager_api.api.domains import router as domains_router
from domainsmanager_api.api.health import router as health_router
from domainsmanager_api.api.oauth import router as oauth_router
from domainsmanager_api.api.notifications import router as notifications_router
from domainsmanager_api.api.tasks import router as tasks_router
from domainsmanager_api.errors import install_exception_handlers
from domainsmanager_api.middleware import RequestIdMiddleware
from domainsmanager_api.resources import Resources, create_resources
from domainsmanager_api.settings import Settings, get_settings
from domainsmanager_persistence.db import run_migrations

ResourceFactory = Callable[[Settings], Resources]


def create_app(
    settings: Settings | None = None,
    *,
    resource_factory: ResourceFactory = create_resources,
) -> FastAPI:
    effective_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await run_migrations(effective_settings.database_config())
        resources = resource_factory(effective_settings)
        app.state.resources = resources
        try:
            if (
                effective_settings.bootstrap_admin_username is not None
                and effective_settings.bootstrap_admin_password is not None
            ):
                await resources.auth.bootstrap_first_admin(
                    effective_settings.bootstrap_admin_username,
                    effective_settings.bootstrap_admin_password.get_secret_value(),
                )
            yield
        finally:
            await resources.close()

    app = FastAPI(
        title=effective_settings.app_name,
        version=effective_settings.app_version,
        docs_url="/docs" if effective_settings.docs_enabled else None,
        redoc_url="/redoc" if effective_settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = effective_settings
    if effective_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=effective_settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match", "X-Request-ID"],
        )
    app.add_middleware(
        RequestIdMiddleware,
        header_name=effective_settings.request_id_header,
    )
    install_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router, prefix=effective_settings.api_prefix)
    app.include_router(oauth_router, prefix=effective_settings.api_prefix)
    app.include_router(admin_router, prefix=effective_settings.api_prefix)
    app.include_router(domains_router, prefix=effective_settings.api_prefix)
    app.include_router(tasks_router, prefix=effective_settings.api_prefix)
    app.include_router(notifications_router, prefix=effective_settings.api_prefix)
    return app


app = create_app()
