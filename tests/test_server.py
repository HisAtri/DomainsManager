import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text

from domainsmanager_api.server import run
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import create_engine


@pytest.mark.asyncio
async def test_complete_server_starts_and_stops_all_background_components(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "server-test.db"
    settings = Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=str(database_path),
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
        started = True

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
    engine = create_engine(settings.database_config())
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT COUNT(*) FROM global_setting"))
                == 0
            )
    finally:
        await engine.dispose()
