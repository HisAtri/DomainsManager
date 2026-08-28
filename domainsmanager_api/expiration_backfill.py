from __future__ import annotations

import argparse
import asyncio
import logging

from domainsmanager_api.resources import create_resources
from domainsmanager_api.settings import get_settings
from domainsmanager_persistence.db import run_migrations

logger = logging.getLogger(__name__)


async def run(*, limit: int) -> int:
    settings = get_settings()
    await run_migrations(settings.database_config())
    resources = create_resources(settings)
    try:
        return await resources.refresh_tasks.enqueue_expiration_backfill(limit=limit)
    finally:
        await resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Queue RDAP lifecycle refreshes for legacy domains."
    )
    parser.add_argument("--limit", type=int, default=500)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    queued = asyncio.run(run(limit=arguments.limit))
    logger.info("queued %s RDAP expiration backfill refresh task(s)", queued)


if __name__ == "__main__":
    main()
