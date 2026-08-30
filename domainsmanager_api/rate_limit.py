from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from time import monotonic
from typing import Protocol

from domainsmanager_api.settings import Settings


class RateLimitConfigurationError(ValueError):
    pass


class RateLimitStore(Protocol):
    async def consume(
        self, subject: str, policy: str, revision: str, quota: int, window_seconds: int
    ) -> tuple[bool, int]: ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    name: str
    quota: int
    window_seconds: int


class MemoryRateLimitStore:
    def __init__(self) -> None:
        self._windows: dict[tuple[str, str, str], tuple[float, int]] = {}
        self._last_cleanup = monotonic()
        self._lock = Lock()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def consume(
        self, subject: str, policy: str, revision: str, quota: int, window_seconds: int
    ) -> tuple[bool, int]:
        now = monotonic()
        key = (subject, policy, revision)
        with self._lock:
            if now - self._last_cleanup >= window_seconds:
                self._windows = {
                    item_key: item
                    for item_key, item in self._windows.items()
                    if now - item[0] < window_seconds
                }
                self._last_cleanup = now
            started_at, count = self._windows.get(key, (now, 0))
            if now - started_at >= window_seconds:
                started_at, count = now, 0
            limited = count >= quota
            if not limited:
                self._windows[key] = (started_at, count + 1)
            retry_after = max(1, int(window_seconds - (now - started_at)))
        return not limited, retry_after


class RedisRateLimitStore:
    _CONSUME = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {count, redis.call('TTL', KEYS[1])}
"""

    def __init__(self, url: str, key_prefix: str) -> None:
        try:
            from redis.asyncio import Redis
        except ImportError as error:
            raise RateLimitConfigurationError(
                "Redis rate limiting requires the rate-limit-redis extra"
            ) from error
        self._client = Redis.from_url(url)
        self._key_prefix = key_prefix

    async def start(self) -> None:
        try:
            await self._client.ping()
        except Exception as error:
            raise RateLimitConfigurationError(
                "Redis rate limiting is configured but Redis is unavailable"
            ) from error

    async def close(self) -> None:
        await self._client.aclose()

    async def consume(
        self, subject: str, policy: str, revision: str, quota: int, window_seconds: int
    ) -> tuple[bool, int]:
        key = f"{self._key_prefix}:{revision}:{policy}:{subject}"
        try:
            count, ttl = await self._client.eval(
                self._CONSUME, 1, key, str(window_seconds)
            )
        except Exception as error:
            raise RuntimeError("Redis rate limiter became unavailable") from error
        return int(count) <= quota, max(1, int(ttl))


class RateLimiter:
    def __init__(self, store: RateLimitStore, settings: Settings) -> None:
        self._store = store
        self.update(settings)

    async def start(self) -> None:
        await self._store.start()

    async def close(self) -> None:
        await self._store.close()

    def update(self, settings: Settings) -> None:
        self._policies = {
            "normal": RateLimitPolicy(
                "normal",
                settings.normal_rate_limit_attempts,
                settings.normal_rate_limit_window_seconds,
            ),
            "expensive": RateLimitPolicy(
                "expensive",
                settings.expensive_rate_limit_attempts,
                settings.expensive_rate_limit_window_seconds,
            ),
        }
        serialized = ";".join(
            f"{policy.name}:{policy.quota}:{policy.window_seconds}"
            for policy in self._policies.values()
        )
        self._revision = sha256(serialized.encode("ascii")).hexdigest()[:16]

    async def consume(self, subject: str, policy_name: str) -> tuple[bool, int]:
        policy = self._policies[policy_name]
        return await self._store.consume(
            subject,
            policy.name,
            self._revision,
            policy.quota,
            policy.window_seconds,
        )


def create_rate_limiter(settings: Settings) -> RateLimiter:
    if settings.rate_limit_backend == "redis":
        if not settings.rate_limit_redis_url:
            raise RateLimitConfigurationError(
                "DOMAINSMANAGER_RATE_LIMIT_REDIS_URL is required for Redis rate limiting"
            )
        store: RateLimitStore = RedisRateLimitStore(
            settings.rate_limit_redis_url, settings.rate_limit_redis_key_prefix
        )
    else:
        store = MemoryRateLimitStore()
    return RateLimiter(store, settings)
