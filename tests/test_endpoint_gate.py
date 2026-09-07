import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from domainsmanager_lookup import DomainLookup, LookupErrorCode
from domainsmanager_lookup._internal.clients.rdap import RdapClient
from domainsmanager_lookup._internal.clients.whois import WhoisClient
from domainsmanager_lookup._internal.errors import (
    LookupFailedError,
    UpstreamRateLimitError,
)
from domainsmanager_lookup._internal.models.registry import RegistryEndpoint
from domainsmanager_lookup._internal.models.response import RawLookupResponse
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.parsers.whois import ProfiledWhoisParser
from domainsmanager_lookup._internal.services.domain_lookup import DomainLookupService
from domainsmanager_lookup.endpoint_gate import EndpointRequestGate, rdap_endpoint_key
from domainsmanager_lookup.memory_store import MemoryLookupStore
from domainsmanager_persistence.db import create_engine, create_session_factory
from domainsmanager_persistence.lookup_store import SqlAlchemyLookupStore
from domainsmanager_persistence.models import Base
from tests.database import sqlite_database


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


@pytest.fixture(params=["memory", "sqlite"])
async def gate_store(request, tmp_path):
    if request.param == "memory":
        yield MemoryLookupStore()
        return
    engine = create_engine(sqlite_database(tmp_path / "gates.db"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield SqlAlchemyLookupStore(create_session_factory(engine))
    finally:
        await engine.dispose()


async def test_long_request_renews_shared_lease_and_cancellation_releases_it(
    gate_store,
):
    gate = EndpointRequestGate(gate_store, lease_duration=timedelta(seconds=0.3))
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def operation():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    running = asyncio.create_task(gate.run("whois", "whois.example:43", operation))
    try:
        await entered.wait()
        await asyncio.sleep(0.65)
        assert (
            await gate_store.try_acquire_endpoint_gate(
                "whois", "whois.example:43", "other", timedelta(seconds=1)
            )
            is None
        )
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert stopped.is_set()
    assert (
        await gate_store.try_acquire_endpoint_gate(
            "whois", "whois.example:43", "other", timedelta(seconds=1)
        )
        is not None
    )


async def test_sustained_rate_limiting_saturates_without_overflow(gate_store):
    for i in range(65):
        lease = await gate_store.try_acquire_endpoint_gate(
            "rdap", "https://rdap.example", "worker", timedelta(seconds=1)
        )
        assert lease is not None
        # Past Retry-After allows deterministic repeated rejections, without sleeping.
        retry_at = datetime.now(UTC) - timedelta(seconds=1) if i < 64 else None
        blocked_until = await gate_store.block_endpoint_gate(
            lease,
            retry_after=retry_at,
            retry_base=timedelta(minutes=1),
            retry_max=timedelta(hours=1),
        )
    assert (
        timedelta(minutes=59) < blocked_until - datetime.now(UTC) <= timedelta(hours=1)
    )


async def test_stale_owner_cannot_release_or_renew_another_request(gate_store):
    old = await gate_store.try_acquire_endpoint_gate(
        "whois", "example:43", "first", timedelta(seconds=-1)
    )
    current = await gate_store.try_acquire_endpoint_gate(
        "whois", "example:43", "second", timedelta(minutes=1)
    )
    assert old is not None and current is not None
    assert not await gate_store.renew_endpoint_gate(old, timedelta(minutes=2))
    await gate_store.release_endpoint_gate(old, succeeded=True)
    assert (
        await gate_store.try_acquire_endpoint_gate(
            "whois", "example:43", "third", timedelta(minutes=1)
        )
        is None
    )


@pytest.mark.parametrize("status", [429, 503])
async def test_rdap_redirect_target_shares_cooldown_with_direct_queries(status):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        if request.url.host == "bootstrap.example":
            return httpx.Response(
                302,
                headers={"Location": "https://target.example/rdap/domain/example.com"},
            )
        return httpx.Response(status, headers={"Retry-After": "120"})

    store = MemoryLookupStore()
    gate = EndpointRequestGate(store)
    domain = DomainNormalizer().normalize("example.com")
    now = datetime.now(UTC)
    endpoint = RegistryEndpoint(
        key="com",
        tld="com",
        rdap_urls=["https://bootstrap.example"],
        fetched_at=now,
        expires_at=now + timedelta(days=1),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = RdapClient(http_client=http, endpoint_gate=gate)
        with pytest.raises(UpstreamRateLimitError):
            await client.query(domain, endpoint)
        with pytest.raises(UpstreamRateLimitError):
            await client.query_related(
                domain, "https://target.example/rdap/domain/another.com"
            )
    assert len(calls) == 2
    state = await store.get_endpoint_gate_state("rdap", "https://target.example/rdap")
    assert state is not None and state.blocked_until > now + timedelta(seconds=119)
    origin = await store.get_endpoint_gate_state("rdap", "https://bootstrap.example")
    assert origin is not None and origin.blocked_until is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://RDAP.Example.:443/rdap/domain/%65xample.com?x=1",
            "https://rdap.example/rdap",
        ),
        ("http://rdap.example:80/rdap/", "http://rdap.example/rdap"),
        ("http://rdap.example:443/rdap", "http://rdap.example:443/rdap"),
        ("https://[::1]:443/rdap/domain/example.com", "https://[::1]/rdap"),
    ],
)
def test_rdap_endpoint_identity(url, expected):
    assert rdap_endpoint_key(url) == expected


def test_huge_retry_after_does_not_overflow():
    assert RdapClient._parse_retry_after(
        "9" * 100, datetime.now(UTC)
    ) == datetime.max.replace(tzinfo=UTC)


def test_structured_rate_limit_is_not_lost_to_fallback_error_text():
    error = LookupFailedError(
        "rdap rate_limited; whois not found", retry_after=datetime.now(UTC)
    )
    assert DomainLookup._classify_error(error) == LookupErrorCode.RATE_LIMITED


@pytest.mark.parametrize("cancel", [True, False])
async def test_whois_cancellation_and_timeout_close_socket_before_gate_release(
    monkeypatch, cancel
):
    reading = asyncio.Event()

    async def read(_size):
        reading.set()
        await asyncio.Event().wait()

    writer = Mock(drain=AsyncMock())
    monkeypatch.setattr(
        asyncio, "open_connection", AsyncMock(return_value=(Mock(read=read), writer))
    )
    store = MemoryLookupStore()
    gate = EndpointRequestGate(store)
    client = WhoisClient(timeout=1 if cancel else 0.03)
    request = asyncio.create_task(
        gate.run(
            "whois", "example:43", lambda: client._query(b"example.com\r\n", "example")
        )
    )
    await reading.wait()
    if cancel:
        request.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await request
    writer.close.assert_called_once()
    assert (
        await store.try_acquire_endpoint_gate(
            "whois", "example:43", "next", timedelta(minutes=1)
        )
        is not None
    )


@pytest.mark.parametrize("maximum", [100, 3])
async def test_whois_reads_chunks_and_enforces_size_limit(monkeypatch, maximum):
    reader = Mock(read=AsyncMock(side_effect=[b"first", b"second", b""]))
    writer = Mock(drain=AsyncMock())
    connection = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(asyncio, "open_connection", connection)
    client = WhoisClient(max_response_bytes=maximum)
    if maximum == 3:
        with pytest.raises(ValueError, match="最大大小"):
            await client._query(b"example.com\r\n", "whois.example")
    else:
        assert (
            await client._query(b"example.com\r\n", "whois.example") == b"firstsecond"
        )
    connection.assert_awaited_once_with("whois.example", 43)
    writer.write.assert_called_once_with(b"example.com\r\n")
    writer.close.assert_called_once()


async def test_empty_timeout_error_is_still_retryable():
    now = datetime.now(UTC)
    endpoint = RegistryEndpoint(
        key="com",
        tld="com",
        whois_server="whois.example",
        rdap_urls=[],
        fetched_at=now,
        expires_at=now + timedelta(days=1),
    )
    service = DomainLookupService(
        endpoint_provider=Mock(discover=AsyncMock(return_value=endpoint)),
        clients={"whois": Mock(query=AsyncMock(side_effect=TimeoutError()))},
        protocol_order=("whois",),
    )
    outcome = (await DomainLookup(service=service).lookup(["example.com"]))[0]
    assert outcome.error_code == LookupErrorCode.TEMPORARY_FAILURE
