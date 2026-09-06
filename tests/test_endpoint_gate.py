import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from domainsmanager_lookup._internal.clients.rdap import RdapClient
from domainsmanager_lookup._internal.errors import UpstreamRateLimitError
from domainsmanager_lookup._internal.models.response import RawLookupResponse
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.parsers.whois import ProfiledWhoisParser
from domainsmanager_lookup.endpoint_gate import EndpointRequestGate
from domainsmanager_lookup.memory_store import MemoryLookupStore


@pytest.mark.asyncio
async def test_endpoint_gate_serializes_requests_to_the_same_endpoint() -> None:
    store = MemoryLookupStore()
    gate = EndpointRequestGate(store, busy_poll_seconds=0.001)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> str:
        first_entered.set()
        await release_first.wait()
        return "first"

    async def second() -> str:
        second_entered.set()
        return "second"

    first_task = asyncio.create_task(gate.run("whois", "whois.example:43", first))
    await first_entered.wait()
    second_task = asyncio.create_task(gate.run("whois", "whois.example:43", second))
    await asyncio.sleep(0.02)
    assert not second_entered.is_set()
    release_first.set()
    assert await asyncio.gather(first_task, second_task) == ["first", "second"]


@pytest.mark.asyncio
async def test_endpoint_cooldown_is_shared_but_protocol_isolated() -> None:
    store = MemoryLookupStore()
    gate = EndpointRequestGate(store, busy_poll_seconds=0.001)
    retry_after = datetime.now(UTC) + timedelta(minutes=5)

    async def limited() -> None:
        raise UpstreamRateLimitError(
            "whois", "whois.example:43", retry_after=retry_after
        )

    with pytest.raises(UpstreamRateLimitError) as first:
        await gate.run("whois", "whois.example:43", limited)
    assert first.value.retry_after == retry_after

    with pytest.raises(UpstreamRateLimitError) as queued:
        await gate.run("whois", "whois.example:43", limited)
    assert queued.value.retry_after == retry_after

    assert await gate.run("rdap", "https://whois.example", lambda: _value("ok")) == "ok"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("120", datetime(2026, 9, 6, 0, 2, tzinfo=UTC)),
        ("Sun, 06 Sep 2026 00:03:00 GMT", datetime(2026, 9, 6, 0, 3, tzinfo=UTC)),
        ("not-a-date", None),
    ],
)
def test_rdap_retry_after_supports_seconds_and_http_dates(
    header: str, expected: datetime | None
) -> None:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    assert RdapClient._parse_retry_after(header, now) == expected


def test_whois_rate_limit_marker_raises_structured_endpoint_error() -> None:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    domain = DomainNormalizer().normalize("example.cn")
    response = RawLookupResponse(
        domain="example.cn",
        protocol="whois",
        endpoint="whois.cnnic.cn",
        body="Query rate limit exceeded",
        fetched_at=now,
        expires_at=now + timedelta(hours=1),
    )

    with pytest.raises(UpstreamRateLimitError) as limited:
        ProfiledWhoisParser().parse(response, domain)
    assert limited.value.protocol == "whois"
    assert limited.value.endpoint == "whois.cnnic.cn"


async def _value(value: str) -> str:
    return value
