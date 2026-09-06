from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TypeVar
from uuid import uuid4

from domainsmanager_lookup._internal.errors import UpstreamRateLimitError
from domainsmanager_lookup.store import LookupStore

T = TypeVar("T")


class EndpointRequestGate:
    """Serialize requests and persist cooldowns for a protocol endpoint."""

    def __init__(
        self,
        store: LookupStore,
        *,
        lease_duration: timedelta = timedelta(minutes=1),
        retry_base: timedelta = timedelta(minutes=1),
        retry_max: timedelta = timedelta(hours=1),
        busy_poll_seconds: float = 0.05,
    ) -> None:
        self._store = store
        self._lease_duration = lease_duration
        self.retry_base = retry_base
        self.retry_max = retry_max
        self._busy_poll_seconds = busy_poll_seconds
        self._owner = str(uuid4())

    async def run(
        self,
        protocol: str,
        endpoint: str,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        while True:
            lease = await self._store.try_acquire_endpoint_gate(
                protocol,
                endpoint,
                self._owner,
                self._lease_duration,
            )
            if lease is not None:
                break
            state = await self._store.get_endpoint_gate_state(protocol, endpoint)
            if state is not None and state.blocked_until is not None:
                from datetime import UTC, datetime

                now = datetime.now(UTC)
                if state.blocked_until > now:
                    raise UpstreamRateLimitError(
                        protocol,
                        endpoint,
                        retry_after=state.blocked_until,
                    )
            await asyncio.sleep(self._busy_poll_seconds)

        try:
            result = await operation()
        except UpstreamRateLimitError as error:
            blocked_until = await self._store.block_endpoint_gate(
                lease,
                retry_after=error.retry_after,
                retry_base=self.retry_base,
                retry_max=self.retry_max,
            )
            raise UpstreamRateLimitError(
                protocol,
                endpoint,
                retry_after=blocked_until,
            ) from error
        except BaseException:
            await self._store.release_endpoint_gate(lease, succeeded=False)
            raise
        else:
            await self._store.release_endpoint_gate(lease, succeeded=True)
            return result
