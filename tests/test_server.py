import asyncio

import pytest

from domainsmanager_api.server import run
from domainsmanager_api.settings import Settings


@pytest.mark.asyncio
async def test_complete_server_starts_and_stops_all_background_components() -> None:
    settings = Settings(
        _env_file=None,
        database_type="sqlite",
        database_path="server-test.db",
        jwt_secret_key="x",
        refresh_token_pepper="y",
    )
    started: list[str] = []
    stopped: list[str] = []

    async def component(*, settings: Settings, stop: asyncio.Event) -> None:
        started.append(settings.app_name)
        await stop.wait()
        stopped.append(settings.app_name)

    class Server:
        async def serve(self) -> None:
            await asyncio.sleep(0)

    await run(
        settings=settings,
        worker_runner=component,
        scheduler_runner=component,
        notifier_runner=component,
        server_factory=lambda _: Server(),  # type: ignore[return-value]
    )

    assert started == [settings.app_name] * 3
    assert stopped == [settings.app_name] * 3
