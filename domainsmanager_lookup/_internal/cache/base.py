from abc import ABC, abstractmethod
from datetime import datetime

from domainsmanager_lookup._internal.models.registry import RegistryEndpoint
from domainsmanager_lookup._internal.models.response import (
    LookupProtocol,
    RawLookupResponse,
    RdapResponseRole,
)


class DomainResponseCache(ABC):
    """原始域名报文缓存接口；数据库实现只需遵守此契约。"""

    @abstractmethod
    async def get_fresh(
        self,
        domain: str,
        protocol: LookupProtocol,
        now: datetime,
        rdap_role: RdapResponseRole | None = None,
    ) -> RawLookupResponse | None:
        raise NotImplementedError

    @abstractmethod
    async def save(self, response: RawLookupResponse) -> None:
        raise NotImplementedError


class RegistryEndpointCache(ABC):
    """注册局 WHOIS/RDAP 端点缓存接口。"""

    @abstractmethod
    async def get_fresh(
        self,
        key: str,
        now: datetime,
    ) -> RegistryEndpoint | None:
        raise NotImplementedError

    @abstractmethod
    async def save(self, endpoint: RegistryEndpoint) -> None:
        raise NotImplementedError
