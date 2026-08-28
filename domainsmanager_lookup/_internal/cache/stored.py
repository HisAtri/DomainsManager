from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from domainsmanager_lookup._internal.cache.base import (
    DomainResponseCache,
    RegistryEndpointCache,
)
from domainsmanager_lookup._internal.cache.serialization import (
    CODEC,
    SCHEMA_VERSION,
    decode_datetime,
    decode_payload,
    encode_datetime,
    encode_payload,
)
from domainsmanager_lookup._internal.models.registry import RegistryEndpoint
from domainsmanager_lookup._internal.models.response import (
    LookupProtocol,
    RawLookupResponse,
    RdapResponseRole,
)
from domainsmanager_lookup.store import LookupStore, StoredLookupRecord


class StoredDomainResponseCache(DomainResponseCache):
    def __init__(self, store: LookupStore) -> None:
        self._store = store

    async def get_fresh(
        self,
        domain: str,
        protocol: LookupProtocol,
        now: datetime,
        rdap_role: RdapResponseRole | None = None,
    ) -> RawLookupResponse | None:
        try:
            record = await self._store.get_current(
                self._namespace(protocol),
                self._cache_key(domain, rdap_role),
            )
            if record is None or record.fresh_until <= now:
                return None
            return self._decode(record)
        except Exception:
            return None

    async def save(self, response: RawLookupResponse) -> None:
        payload, content_hash = encode_payload(
            {
                "domain": response.domain,
                "protocol": response.protocol,
                "endpoint": response.endpoint,
                "body": response.body,
                "fetched_at": encode_datetime(response.fetched_at),
                "expires_at": encode_datetime(response.expires_at),
                "status_code": response.status_code,
                "content_type": response.content_type,
                "rdap_role": response.rdap_role,
            }
        )
        record = StoredLookupRecord(
            record_id=uuid4(),
            namespace=self._namespace(response.protocol),
            cache_key=self._cache_key(response.domain, response.rdap_role),
            schema_version=SCHEMA_VERSION,
            payload=payload,
            payload_codec=CODEC,
            content_hash=content_hash,
            observed_at=response.fetched_at,
            fresh_until=response.expires_at,
            protocol=response.protocol,
            endpoint=response.endpoint,
            status_code=response.status_code,
        )
        await self._store.publish(record)

    async def mark_unusable(self, response: RawLookupResponse, reason: str) -> None:
        record = await self._store.get_current(
            self._namespace(response.protocol),
            self._cache_key(response.domain, response.rdap_role),
        )
        if record is not None and record.content_hash == self._hash_response(response):
            await self._store.mark_unusable(record.record_id, reason)

    @staticmethod
    def _namespace(protocol: LookupProtocol) -> str:
        return f"response:{protocol}:v1"

    @staticmethod
    def _cache_key(domain: str, rdap_role: RdapResponseRole | None) -> str:
        return f"{domain}:{rdap_role or 'default'}"

    @staticmethod
    def _decode(record: StoredLookupRecord) -> RawLookupResponse:
        if record.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported response cache schema")
        data = decode_payload(record.payload, record.payload_codec)
        return RawLookupResponse(
            domain=data["domain"],
            protocol=data["protocol"],
            endpoint=data["endpoint"],
            body=data["body"],
            fetched_at=decode_datetime(data["fetched_at"]),
            expires_at=decode_datetime(data["expires_at"]),
            status_code=data.get("status_code"),
            content_type=data.get("content_type"),
            rdap_role=data.get("rdap_role"),
        )

    @staticmethod
    def _hash_response(response: RawLookupResponse) -> str:
        _, content_hash = encode_payload(
            {
                "domain": response.domain,
                "protocol": response.protocol,
                "endpoint": response.endpoint,
                "body": response.body,
                "fetched_at": encode_datetime(response.fetched_at),
                "expires_at": encode_datetime(response.expires_at),
                "status_code": response.status_code,
                "content_type": response.content_type,
                "rdap_role": response.rdap_role,
            }
        )
        return content_hash


class StoredRegistryEndpointCache(RegistryEndpointCache):
    NAMESPACE = "registry-endpoint:v1"

    def __init__(self, store: LookupStore) -> None:
        self._store = store

    async def get_fresh(
        self,
        key: str,
        now: datetime,
    ) -> RegistryEndpoint | None:
        try:
            record = await self._store.get_current(self.NAMESPACE, key)
            if record is None or record.fresh_until <= now:
                return None
            return self._decode(record)
        except Exception:
            return None

    async def save(self, endpoint: RegistryEndpoint) -> None:
        payload, content_hash = encode_payload(
            {
                "key": endpoint.key,
                "tld": endpoint.tld,
                "whois_server": endpoint.whois_server,
                "rdap_urls": endpoint.rdap_urls,
                "source": endpoint.source,
                "fetched_at": encode_datetime(endpoint.fetched_at),
                "expires_at": encode_datetime(endpoint.expires_at),
            }
        )
        await self._store.publish(
            StoredLookupRecord(
                record_id=uuid4(),
                namespace=self.NAMESPACE,
                cache_key=endpoint.key,
                schema_version=SCHEMA_VERSION,
                payload=payload,
                payload_codec=CODEC,
                content_hash=content_hash,
                observed_at=endpoint.fetched_at,
                fresh_until=endpoint.expires_at,
                endpoint=endpoint.whois_server,
            )
        )

    @staticmethod
    def _decode(record: StoredLookupRecord) -> RegistryEndpoint:
        if record.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported endpoint cache schema")
        data = decode_payload(record.payload, record.payload_codec)
        return RegistryEndpoint(
            key=data["key"],
            tld=data["tld"],
            whois_server=data.get("whois_server"),
            rdap_urls=data.get("rdap_urls", []),
            source=data.get("source", "iana"),
            fetched_at=decode_datetime(data["fetched_at"]),
            expires_at=decode_datetime(data["expires_at"]),
        )
