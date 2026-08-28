"""Explicit, opt-in cleanup for expired operational records."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domainsmanager_api.settings import get_settings
from domainsmanager_persistence.db import create_engine, create_session_factory
from domainsmanager_persistence.models import (
    AuthRefreshToken,
    AuthSession,
    DomainRefreshTask,
    IdempotencyRecord,
    LookupRefreshLease,
    NotificationOutbox,
    SecurityAuditEvent,
)

TARGETS = (
    "sessions",
    "refresh_tokens",
    "idempotency",
    "tasks",
    "notifications",
    "audit",
    "leases",
)
TERMINAL_TASK_STATUSES = ("success", "info", "warning", "failed")
TERMINAL_NOTIFICATION_STATUSES = ("sent", "dead_letter")


@dataclass(frozen=True, slots=True)
class CleanupResult:
    target: str
    count: int


def parse_before(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("must include a timezone")
    return parsed.astimezone(UTC)


def cleanup_statements(before: datetime) -> dict[str, object]:
    return {
        "sessions": AuthSession.absolute_expires_at < before,
        "refresh_tokens": AuthRefreshToken.expires_at < before,
        "idempotency": IdempotencyRecord.expires_at < before,
        "tasks": DomainRefreshTask.completed_at < before,
        "notifications": NotificationOutbox.updated_at < before,
        "audit": SecurityAuditEvent.occurred_at < before,
        "leases": LookupRefreshLease.lease_until < before,
    }


def cleanup_models() -> dict[str, type]:
    return {
        "sessions": AuthSession,
        "refresh_tokens": AuthRefreshToken,
        "idempotency": IdempotencyRecord,
        "tasks": DomainRefreshTask,
        "notifications": NotificationOutbox,
        "audit": SecurityAuditEvent,
        "leases": LookupRefreshLease,
    }


async def run_cleanup(
    sessions: async_sessionmaker[AsyncSession],
    *,
    before: datetime,
    targets: Sequence[str],
    apply: bool,
) -> list[CleanupResult]:
    selected = tuple(dict.fromkeys(targets))
    unknown = set(selected) - set(TARGETS)
    if unknown:
        raise ValueError(f"unknown cleanup targets: {sorted(unknown)}")
    statements = cleanup_statements(before)
    models = cleanup_models()
    filters = {
        "tasks": statements["tasks"] & DomainRefreshTask.status.in_(TERMINAL_TASK_STATUSES),
        "notifications": statements["notifications"]
        & NotificationOutbox.status.in_(TERMINAL_NOTIFICATION_STATUSES),
    }
    results: list[CleanupResult] = []
    async with sessions() as session, session.begin():
        for target in selected:
            model = models[target]
            condition = filters.get(target, statements[target])
            count = await session.scalar(
                select(func.count()).select_from(model).where(condition)
            )
            results.append(CleanupResult(target=target, count=count or 0))
        if apply:
            for target in selected:
                model = models[target]
                condition = filters.get(target, statements[target])
                await session.execute(delete(model).where(condition))
    return results


async def run_from_args(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_config())
    try:
        results = await run_cleanup(
            create_session_factory(engine),
            before=args.before,
            targets=args.targets,
            apply=args.apply,
        )
    finally:
        await engine.dispose()
    mode = "applied" if args.apply else "dry-run"
    for result in results:
        print(f"{mode} {result.target}: {result.count}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean expired DomainsManager records")
    parser.add_argument("--before", required=True, type=parse_before)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("targets", choices=TARGETS, nargs="+")
    raise SystemExit(asyncio.run(run_from_args(parser.parse_args())))
