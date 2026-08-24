"""Run the complete DomainsManager backend in one process."""

from __future__ import annotations

import asyncio
from asyncio import Event
from collections.abc import Awaitable, Callable

import uvicorn

from domainsmanager_api.main import create_app
from domainsmanager_api.notifier_worker import run as run_notifier
from domainsmanager_api.scheduler import run as run_scheduler
from domainsmanager_api.settings import Settings, get_settings
from domainsmanager_api.worker import run as run_worker

BackgroundRunner = Callable[..., Awaitable[None]]


async def run(
    *,
    settings: Settings | None = None,
    worker_runner: BackgroundRunner = run_worker,
    scheduler_runner: BackgroundRunner = run_scheduler,
    notifier_runner: BackgroundRunner = run_notifier,
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
    background = [
        asyncio.create_task(worker_runner(settings=effective_settings, stop=stop)),
        asyncio.create_task(scheduler_runner(settings=effective_settings, stop=stop)),
        asyncio.create_task(notifier_runner(settings=effective_settings, stop=stop)),
    ]
    try:
        await server.serve()
    finally:
        stop.set()
        await asyncio.gather(*background, return_exceptions=True)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
