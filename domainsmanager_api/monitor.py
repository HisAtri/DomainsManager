"""Emit aggregate queue metrics for a scheduler or monitoring collector."""

from __future__ import annotations

import asyncio
import logging

from domainsmanager_api.operations import (
    collect_operational_metrics,
    emit_operational_events,
)
from domainsmanager_api.settings import get_settings
from domainsmanager_persistence.db import create_engine, create_session_factory


async def run() -> int:
    settings = get_settings()
    engine = create_engine(settings.database_config())
    try:
        async with create_session_factory(engine)() as session:
            metrics = await collect_operational_metrics(session)
    finally:
        await engine.dispose()
    emit_operational_events(metrics, logging.getLogger("domainsmanager.operations"))
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(asyncio.run(run()))
