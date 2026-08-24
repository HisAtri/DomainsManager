from __future__ import annotations

import asyncio
from asyncio import Event
from collections.abc import Awaitable, Callable

from domainsmanager_api.resources import Resources, create_resources
from domainsmanager_api.settings import Settings, get_settings
from domainsmanager_api.worker import default_worker_id
from domainsmanager_persistence.db import run_migrations


async def create_notifier_resources(settings: Settings) -> Resources:
    await run_migrations(settings.database_config())
    return create_resources(settings)


async def run(
    *,
    settings: Settings | None = None,
    stop: Event | None = None,
    resource_factory: Callable[[Settings], Awaitable[Resources]] = create_notifier_resources,
) -> None:
    effective = settings or get_settings()
    resources = await resource_factory(effective)
    stopped = stop or Event()
    try:
        while not stopped.is_set():
            if not await resources.notifier.run_once(default_worker_id()):
                try:
                    await asyncio.wait_for(stopped.wait(), effective.notification_worker_poll_interval_seconds)
                except TimeoutError:
                    pass
    finally:
        await resources.close()


def main() -> None:
    asyncio.run(run())
