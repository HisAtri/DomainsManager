from __future__ import annotations

import os
from uuid import uuid4

import pytest

from domainsmanager_api.rate_limit import RedisRateLimitStore


@pytest.mark.asyncio
@pytest.mark.redis
async def test_redis_store_shares_windows_between_instances() -> None:
    url = os.environ.get("DOMAINSMANAGER_TEST_REDIS_URL")
    if not url:
        pytest.skip("Redis tests require DOMAINSMANAGER_TEST_REDIS_URL")
    prefix = f"domainsmanager:test-rate-limit:{uuid4()}"
    first = RedisRateLimitStore(url, prefix)
    second = RedisRateLimitStore(url, prefix)
    await first.start()
    await second.start()
    try:
        assert (await first.consume("user", "normal", "revision", 2, 60))[0]
        assert (await second.consume("user", "normal", "revision", 2, 60))[0]
        assert not (await first.consume("user", "normal", "revision", 2, 60))[0]
        assert (await second.consume("user", "normal", "new-revision", 2, 60))[0]
    finally:
        await first.close()
        await second.close()
