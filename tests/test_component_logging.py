import json
import logging

import pytest

from domainsmanager_api.component_logging import run_component_cycle


@pytest.mark.asyncio
async def test_component_cycle_logs_completed_work(caplog) -> None:
    async def operation() -> int:
        return 2

    with caplog.at_level(logging.INFO, logger="domainsmanager.components"):
        assert await run_component_cycle("scheduler", "scheduler-1", operation()) == 2

    event = json.loads(caplog.records[0].message)
    assert event["event"] == "component_cycle_completed"
    assert event["component"] == "scheduler"
    assert event["instance_id"] == "scheduler-1"
    assert event["processed"] == 2
    assert event["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_component_cycle_logs_only_exception_type(caplog) -> None:
    async def operation() -> bool:
        raise RuntimeError("credential-value-must-not-be-logged")

    with (
        caplog.at_level(logging.ERROR, logger="domainsmanager.components"),
        pytest.raises(RuntimeError),
    ):
        await run_component_cycle("worker", "worker-1", operation())

    event = json.loads(caplog.records[0].message)
    assert event["event"] == "component_cycle_failed"
    assert event["error_type"] == "RuntimeError"
    assert "credential-value" not in caplog.text
