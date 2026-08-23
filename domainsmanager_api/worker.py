from __future__ import annotations

import asyncio
import os
from asyncio import Event

from domainsmanager_api.resources import create_resources
from domainsmanager_api.settings import get_settings


async def run() -> None:
    settings = get_settings()
    resources = create_resources(settings)
    worker_id = os.environ.get("DOMAINSMANAGER_WORKER_ID", "local-worker")
    stop = Event()
    try:
        while not stop.is_set():
            ran = await resources.tasks.run_once(worker_id)
            if not ran:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass
    finally:
        await resources.close()


def main() -> None:
    asyncio.run(run())
