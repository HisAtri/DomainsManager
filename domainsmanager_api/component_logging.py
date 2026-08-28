"""Structured, low-noise events for background component work cycles."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from time import perf_counter

logger = logging.getLogger("domainsmanager.components")


async def run_component_cycle[T](
    component: str,
    instance_id: str,
    operation: Awaitable[T],
) -> T:
    started_at = perf_counter()
    try:
        result = await operation
    except Exception as error:
        logger.error(
            json.dumps(
                {
                    "event": "component_cycle_failed",
                    "component": component,
                    "instance_id": instance_id,
                    "error_type": type(error).__name__,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
                separators=(",", ":"),
            )
        )
        raise
    if result:
        logger.info(
            json.dumps(
                {
                    "event": "component_cycle_completed",
                    "component": component,
                    "instance_id": instance_id,
                    "processed": int(result),
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
                separators=(",", ":"),
            )
        )
    return result
