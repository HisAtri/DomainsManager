from __future__ import annotations

import asyncio
import os
import socket
from asyncio import Event
from collections.abc import Awaitable, Callable

from domainsmanager_api.resources import Resources, create_resources
from domainsmanager_api.settings import Settings, get_settings
from domainsmanager_persistence.db import run_migrations


async def create_worker_resources(settings: Settings) -> Resources:
    await run_migrations(settings.database_config())
    return create_resources(settings)


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"[:128]


async def run(
    *,
    settings: Settings | None = None,
    stop: Event | None = None,
    resource_factory: Callable[[Settings], Awaitable[Resources]] = create_worker_resources,
) -> None:
    effective_settings = settings or get_settings()
    resources = await resource_factory(effective_settings)
    if isinstance(resources, Resources):
        effective_settings = await resources.reload_global_policies()
    worker_id = os.environ.get("DOMAINSMANAGER_WORKER_ID", default_worker_id())
    effective_stop = stop or Event()
    try:
        while not effective_stop.is_set():
            ran = await resources.tasks.run_once(worker_id)
            if not ran:
                try:
                    await asyncio.wait_for(
                        effective_stop.wait(),
                        timeout=effective_settings.worker_poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
    finally:
        await resources.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
