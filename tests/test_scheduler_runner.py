from asyncio import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from domainsmanager_api.scheduler import run
from domainsmanager_api.settings import Settings


@pytest.mark.asyncio
async def test_scheduler_spreads_overdue_domains_before_first_poll() -> None:
    stop = Event()
    calls: list[str] = []

    async def spread_overdue() -> int:
        calls.append("spread")
        return 2

    async def run_once() -> int:
        calls.append("poll")
        stop.set()
        return 0

    scheduler = SimpleNamespace(
        spread_overdue=spread_overdue,
        run_once=run_once,
    )
    resources = SimpleNamespace(scheduler=scheduler, close=AsyncMock())
    factory = AsyncMock(return_value=resources)
    settings = Settings(
        _env_file=None,
        jwt_secret_key="x",
        refresh_token_pepper="y",
        scheduler_poll_interval_seconds=0.1,
    )

    await run(settings=settings, stop=stop, resource_factory=factory)

    assert calls == ["spread", "poll"]
    resources.close.assert_awaited_once()
