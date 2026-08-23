from asyncio import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from domainsmanager_api.settings import Settings
from domainsmanager_api.worker import default_worker_id, run


def test_default_worker_id_contains_a_process_suffix() -> None:
    worker_id = default_worker_id()

    assert worker_id
    assert worker_id.rsplit("-", maxsplit=1)[-1].isdigit()
    assert len(worker_id) <= 128


@pytest.mark.asyncio
async def test_worker_uses_configured_polling_and_closes_resources() -> None:
    stop = Event()
    tasks = SimpleNamespace()

    async def run_once(_: str) -> bool:
        stop.set()
        return False

    tasks.run_once = run_once
    resources = SimpleNamespace(tasks=tasks, close=AsyncMock())
    factory = AsyncMock(return_value=resources)
    settings = Settings(
        _env_file=None,
        jwt_secret_key="x",
        refresh_token_pepper="y",
        worker_poll_interval_seconds=0.1,
    )

    await run(settings=settings, stop=stop, resource_factory=factory)

    factory.assert_awaited_once_with(settings)
    resources.close.assert_awaited_once()
