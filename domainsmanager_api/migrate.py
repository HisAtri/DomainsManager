"""Run database migrations as a dedicated operational command."""

from __future__ import annotations

import argparse
import asyncio

from domainsmanager_api.settings import Settings, get_settings
from domainsmanager_persistence.db import run_migrations


async def run(settings: Settings, revision: str = "head") -> None:
    await run_migrations(settings.database_config(), revision)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DomainsManager database migrations")
    parser.add_argument("revision", nargs="?", default="head")
    args = parser.parse_args()
    asyncio.run(run(get_settings(), args.revision))
