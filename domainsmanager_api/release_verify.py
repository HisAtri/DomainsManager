"""Read-only release preflight for database readiness and queue state."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from domainsmanager_api.operations import alert_events, collect_operational_metrics
from domainsmanager_api.resources import create_resources
from domainsmanager_api.settings import Settings, get_settings


async def run(
    settings: Settings | None = None,
    *,
    output: Callable[[str], None] = print,
) -> int:
    resources = create_resources(settings or get_settings())
    try:
        if not await resources.database_ready():
            output(
                json.dumps(
                    {
                        "event": "release_preflight",
                        "ready": False,
                        "reason": "database_not_ready",
                    },
                    separators=(",", ":"),
                )
            )
            return 1
        async with resources.sessions() as session:
            metrics = await collect_operational_metrics(session)
        output(
            json.dumps(
                {
                    "event": "release_preflight",
                    "ready": True,
                    "operational_metrics": metrics.payload(),
                    "alerts": list(alert_events(metrics)),
                },
                separators=(",", ":"),
            )
        )
        return 0
    finally:
        await resources.close()


def main() -> None:
    raise SystemExit(asyncio.run(run()))
