import asyncio
from collections.abc import Sequence

from domainsmanager_lookup._internal.cache.stored import (
    StoredDomainResponseCache,
    StoredRegistryEndpointCache,
)
from domainsmanager_lookup._internal.errors import (
    DomainNormalizationError,
    LookupFailedError,
)
from domainsmanager_lookup._internal.models.domain import DomainInfo, NormalizedDomain
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.services.domain_lookup import DomainLookupService
from domainsmanager_lookup.exceptions import InvalidDomainError
from domainsmanager_lookup.memory_store import MemoryLookupStore
from domainsmanager_lookup.store import LookupStore
from domainsmanager_lookup.types import (
    DomainIdentity,
    DomainSnapshot,
    LookupErrorCode,
    LookupOptions,
    LookupOutcome,
    RegistrarSnapshot,
)


class DomainLookup:
    """Stable facade for domain normalization and registry lookups."""

    def __init__(
        self,
        *,
        store: LookupStore | None = None,
        service: DomainLookupService | None = None,
        normalizer: DomainNormalizer | None = None,
    ) -> None:
        if service is None:
            effective_store = store or MemoryLookupStore()
            service = DomainLookupService(
                response_cache=StoredDomainResponseCache(effective_store),
                endpoint_cache=StoredRegistryEndpointCache(effective_store),
                normalizer=normalizer,
            )
        self._service = service
        self._normalizer = normalizer or self._service._normalizer

    def normalize(self, name: str) -> DomainIdentity:
        try:
            normalized = self._normalizer.normalize(name)
        except DomainNormalizationError as exc:
            raise InvalidDomainError(str(exc)) from exc
        return self._identity(normalized)

    async def lookup(
        self,
        names: Sequence[str],
        *,
        options: LookupOptions | None = None,
    ) -> list[LookupOutcome]:
        effective = options or LookupOptions()
        semaphore = asyncio.Semaphore(effective.concurrency)

        async def lookup_one(name: str) -> LookupOutcome:
            try:
                async with semaphore:
                    result = await self._service.lookup(
                        name,
                        force_refresh=effective.force_refresh,
                        refresh_endpoint=effective.refresh_endpoint,
                    )
                return LookupOutcome(
                    input_name=name,
                    identity=self._identity(result.domain),
                    snapshot=self._snapshot(result.info),
                )
            except DomainNormalizationError as exc:
                return LookupOutcome(
                    input_name=name,
                    error_code=LookupErrorCode.INVALID_DOMAIN,
                    error_message=str(exc),
                )
            except LookupFailedError as exc:
                return LookupOutcome(
                    input_name=name,
                    error_code=self._classify_error(exc),
                    error_message=str(exc),
                )

        return list(await asyncio.gather(*(lookup_one(name) for name in names)))

    @staticmethod
    def _identity(domain: NormalizedDomain) -> DomainIdentity:
        return DomainIdentity(
            ascii_name=domain.ascii_name,
            unicode_name=domain.unicode_name,
            registrable_domain=domain.registrable_domain,
            public_suffix=domain.public_suffix,
            tld=domain.tld,
        )

    @staticmethod
    def _snapshot(info: DomainInfo) -> DomainSnapshot:
        registrar = None
        if info.registrar is not None:
            registrar = RegistrarSnapshot.model_validate(info.registrar.model_dump())
        return DomainSnapshot(
            domain=info.domain,
            registrar=registrar,
            statuses=info.statuses,
            registered_at=info.dates.registered_at,
            expires_at=info.dates.expires_at,
            updated_at=info.dates.updated_at,
            nameservers=info.nameservers,
            dnssec_enabled=info.dnssec.enabled,
            source=info.source,
            source_url=info.source_url,
            fetched_at=info.fetched_at,
        )

    @staticmethod
    def _classify_error(error: LookupFailedError) -> LookupErrorCode:
        message = str(error).casefold()
        if "not_found" in message or "not found" in message or "未注册" in message:
            return LookupErrorCode.NOT_FOUND
        if "rate_limited" in message or "rate limit" in message or "限流" in message:
            return LookupErrorCode.RATE_LIMITED
        if "不支持" in message or "没有可用" in message or "profile" in message:
            return LookupErrorCode.UNSUPPORTED
        if "temporary" in message or "timeout" in message or "超时" in message:
            return LookupErrorCode.TEMPORARY_FAILURE
        return LookupErrorCode.UNEXPECTED_RESPONSE
