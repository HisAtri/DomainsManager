"""Emit safe aggregate alerts for explicit security-audit windows."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select

from domainsmanager_api.settings import get_settings
from domainsmanager_persistence.db import create_engine, create_session_factory
from domainsmanager_persistence.models import SecurityAuditEvent

REPLAY_EVENT = "session.refresh_replayed"


def parse_since(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("must include a timezone")
    return parsed.astimezone(UTC)


async def run(since: datetime) -> int:
    engine = create_engine(get_settings().database_config())
    try:
        async with create_session_factory(engine)() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(SecurityAuditEvent)
                .where(
                    SecurityAuditEvent.event_type == REPLAY_EVENT,
                    SecurityAuditEvent.occurred_at >= since,
                )
            )
    finally:
        await engine.dispose()
    event = {
        "event": "security_audit_metrics",
        "since": since.isoformat(),
        "refresh_token_replays": count or 0,
    }
    logging.getLogger("domainsmanager.security").info(
        json.dumps(event, separators=(",", ":"))
    )
    if count:
        logging.getLogger("domainsmanager.security").warning(
            json.dumps(
                {
                    "event": "security_alert",
                    "alert": "refresh_token_replayed",
                    "count": count,
                    "since": since.isoformat(),
                },
                separators=(",", ":"),
            )
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit security audit alerts")
    parser.add_argument("--since", required=True, type=parse_since)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(asyncio.run(run(parser.parse_args().since)))
