from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from domainsmanager_api.api.health import router as health_router
from domainsmanager_api.errors import install_exception_handlers
from domainsmanager_api.middleware import RequestIdMiddleware
from domainsmanager_api.resources import Resources, create_resources
from domainsmanager_api.settings import Settings, get_settings

ResourceFactory = Callable[[Settings], Resources]


def create_app(
    settings: Settings | None = None,
    *,
    resource_factory: ResourceFactory = create_resources,
) -> FastAPI:
    effective_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources = resource_factory(effective_settings)
        app.state.resources = resources
        try:
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
    app.add_middleware(
        RequestIdMiddleware,
        header_name=effective_settings.request_id_header,
    )
    install_exception_handlers(app)
    app.include_router(health_router)
    return app


app = create_app()
