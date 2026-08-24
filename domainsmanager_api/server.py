"""Run the complete DomainsManager backend in one process."""

from __future__ import annotations

import asyncio
from asyncio import Event
from collections.abc import Awaitable, Callable

import uvicorn

from domainsmanager_api.main import create_app
from domainsmanager_api.notifier_worker import run as run_notifier
from domainsmanager_api.resources import Resources, create_resources
from domainsmanager_api.scheduler import run as run_scheduler
from domainsmanager_api.settings import Settings, get_settings
from domainsmanager_api.worker import run as run_worker

BackgroundRunner = Callable[..., Awaitable[None]]


async def create_background_resources(settings: Settings) -> Resources:
    """Create isolated component resources after the API has migrated the DB."""
    return create_resources(settings)


async def run_worker_after_startup(*, settings: Settings, stop: Event) -> None:
    await run_worker(
        settings=settings,
        stop=stop,
        resource_factory=create_background_resources,
    )


async def run_scheduler_after_startup(*, settings: Settings, stop: Event) -> None:
    await run_scheduler(
        settings=settings,
        stop=stop,
        resource_factory=create_background_resources,
    )


async def run_notifier_after_startup(*, settings: Settings, stop: Event) -> None:
    await run_notifier(
        settings=settings,
        stop=stop,
        resource_factory=create_background_resources,
    )


async def run(
    *,
    settings: Settings | None = None,
    worker_runner: BackgroundRunner = run_worker_after_startup,
    scheduler_runner: BackgroundRunner = run_scheduler_after_startup,
    notifier_runner: BackgroundRunner = run_notifier_after_startup,
    server_factory: Callable[[uvicorn.Config], uvicorn.Server] = uvicorn.Server,
) -> None:
    """Serve HTTP and process refresh, scheduling, and notification work."""
    effective_settings = settings or get_settings()
    stop = Event()
    config = uvicorn.Config(
        create_app(effective_settings),
        host=effective_settings.server_host,
        port=effective_settings.server_port,
    )
    server = server_factory(config)
    background: list[asyncio.Task[None]] = []
    server_task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if server_task.done():
                await server_task
            await asyncio.sleep(0.01)
        background = [
            asyncio.create_task(worker_runner(settings=effective_settings, stop=stop)),
            asyncio.create_task(scheduler_runner(settings=effective_settings, stop=stop)),
            asyncio.create_task(notifier_runner(settings=effective_settings, stop=stop)),
        ]
        await server_task
    finally:
        stop.set()
        if not server_task.done():
            server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await asyncio.gather(*background, return_exceptions=True)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
