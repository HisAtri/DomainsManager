import ast
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import domainsmanager_lookup
from domainsmanager_lookup import (
    DomainLookup,
    InvalidDomainError,
    LookupErrorCode,
    LookupOptions,
)
from domainsmanager_lookup._internal.errors import (
    DomainNormalizationError,
    LookupFailedError,
)
from domainsmanager_lookup._internal.models.domain import (
    DNSSECInfo,
    DomainDates,
    DomainInfo,
    NormalizedDomain,
)
from domainsmanager_lookup._internal.models.response import LookupResult, RawLookupResponse
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.services.domain_lookup import DomainLookupService

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_result(name: str) -> LookupResult:
    domain = DomainNormalizer().normalize(name)
    response = RawLookupResponse(
        domain=domain.registrable_domain,
        protocol="rdap",
        endpoint="https://rdap.example",
        body="{}",
        fetched_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    return LookupResult(
        domain=domain,
        info=DomainInfo(
            domain=domain.registrable_domain,
            statuses=["active"],
            dates=DomainDates(expires_at=NOW + timedelta(days=30)),
            dnssec=DNSSECInfo(enabled=True),
            source="rdap",
            fetched_at=NOW,
        ),
        response=response,
    )


class FakeService:
    def __init__(self) -> None:
        self._normalizer = DomainNormalizer()
        self.active = 0
        self.max_active = 0

    async def lookup(self, name: str, **kwargs) -> LookupResult:
        if name == "invalid":
            raise DomainNormalizationError("invalid domain")
        if name == "missing.example":
            raise LookupFailedError("whois status not_found")
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        return make_result(name)


@pytest.mark.unit
def test_normalize_returns_stable_identity() -> None:
    identity = DomainLookup().normalize("WWW.BÜCHER.DE.")

    assert identity.ascii_name == "www.xn--bcher-kva.de"
    assert identity.registrable_domain == "xn--bcher-kva.de"
    assert identity.public_suffix == "de"


@pytest.mark.unit
def test_normalize_maps_internal_error() -> None:
    with pytest.raises(InvalidDomainError):
        DomainLookup().normalize("")


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.concurrency
async def test_lookup_preserves_order_limits_concurrency_and_returns_partial_failures() -> None:
    service = FakeService()
    lookup = DomainLookup(service=service)

    outcomes = await lookup.lookup(
        ["example.com", "invalid", "missing.example", "example.net"],
        options=LookupOptions(concurrency=2),
    )

    assert [item.input_name for item in outcomes] == [
        "example.com",
        "invalid",
        "missing.example",
        "example.net",
    ]
    assert outcomes[0].succeeded
    assert outcomes[1].error_code is LookupErrorCode.INVALID_DOMAIN
    assert outcomes[2].error_code is LookupErrorCode.NOT_FOUND
    assert outcomes[3].succeeded
    assert service.max_active <= 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_lookup_empty_input_returns_empty_list() -> None:
    assert await DomainLookup(service=FakeService()).lookup([]) == []


@pytest.mark.contract
def test_root_exports_are_explicit_and_internal_types_are_hidden() -> None:
    assert domainsmanager_lookup.__all__ == [
        "DomainIdentity",
        "DomainLookup",
        "DomainLookupError",
        "DomainSnapshot",
        "InvalidDomainError",
        "LookupErrorCode",
        "LookupOptions",
        "LookupOutcome",
    ]
    assert not hasattr(domainsmanager_lookup, "RawLookupResponse")
    assert not hasattr(domainsmanager_lookup, "RdapClient")


@pytest.mark.compat
def test_legacy_service_is_same_class() -> None:
    from modules.services import DomainLookupService as LegacyService

    assert LegacyService is DomainLookupService


@pytest.mark.package
def test_internal_package_does_not_import_legacy_modules() -> None:
    root = Path(__file__).parents[1] / "domainsmanager_lookup"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name == "modules" or name.startswith("modules.") for name in names):
                violations.append(str(path.relative_to(root)))
    assert violations == []
