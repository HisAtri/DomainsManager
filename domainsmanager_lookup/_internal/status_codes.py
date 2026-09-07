from collections.abc import Iterable
from dataclasses import dataclass


def _normalized_key(value: str) -> str:
    return " ".join(value.split()).casefold()


@dataclass(frozen=True, slots=True)
class DomainStatusCode:
    """One domain status expressed in both EPP and RDAP vocabularies."""

    epp_code: str
    rdap_code: str

    def __post_init__(self) -> None:
        epp_code = " ".join(self.epp_code.split())
        rdap_code = _normalized_key(self.rdap_code)
        if not epp_code:
            raise ValueError("EPP status code must not be empty")
        if not rdap_code:
            raise ValueError("RDAP status code must not be empty")
        object.__setattr__(self, "epp_code", epp_code)
        object.__setattr__(self, "rdap_code", rdap_code)


class DomainStatusCodeRegistry:
    """Resolve and normalize domain statuses through EPP and RDAP indexes."""

    def __init__(self, codes: Iterable[DomainStatusCode]) -> None:
        self._codes = tuple(codes)
        self._by_epp: dict[str, DomainStatusCode] = {}
        self._by_rdap: dict[str, DomainStatusCode] = {}

        for code in self._codes:
            self._add_index(self._by_epp, code.epp_code, code, "EPP")
            self._add_index(self._by_rdap, code.rdap_code, code, "RDAP")

        for key in self._by_epp.keys() & self._by_rdap.keys():
            if self._by_epp[key] is not self._by_rdap[key]:
                raise ValueError(f"ambiguous status code {key!r} across EPP and RDAP")

    @property
    def codes(self) -> tuple[DomainStatusCode, ...]:
        return self._codes

    def get_by_epp(self, epp_code: str) -> DomainStatusCode | None:
        return self._by_epp.get(_normalized_key(epp_code))

    def get_by_rdap(self, rdap_code: str) -> DomainStatusCode | None:
        return self._by_rdap.get(_normalized_key(rdap_code))

    def resolve(self, value: str) -> DomainStatusCode | None:
        key = _normalized_key(value)
        return self._by_epp.get(key) or self._by_rdap.get(key)

    def normalize(self, value: str) -> str | None:
        key = _normalized_key(value)
        if not key:
            return None
        resolved = self.resolve(key)
        return resolved.rdap_code if resolved is not None else key

    def normalize_many(self, values: Iterable[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            status = self.normalize(value)
            if status is not None and status not in seen:
                seen.add(status)
                normalized.append(status)
        return normalized

    @staticmethod
    def _add_index(
        index: dict[str, DomainStatusCode],
        value: str,
        code: DomainStatusCode,
        vocabulary: str,
    ) -> None:
        key = _normalized_key(value)
        if key in index:
            raise ValueError(f"duplicate {vocabulary} status code {value!r}")
        index[key] = code


# https://www.icann.org/resources/pages/epp-status-codes-2014-06-16-en
STANDARD_DOMAIN_STATUS_CODES = (
    DomainStatusCode("addPeriod", "add period"),
    DomainStatusCode("autoRenewPeriod", "auto renew period"),
    DomainStatusCode("inactive", "inactive"),
    DomainStatusCode("ok", "active"),
    DomainStatusCode("pendingCreate", "pending create"),
    DomainStatusCode("pendingDelete", "pending delete"),
    DomainStatusCode("pendingRenew", "pending renew"),
    DomainStatusCode("pendingRestore", "pending restore"),
    DomainStatusCode("pendingTransfer", "pending transfer"),
    DomainStatusCode("pendingUpdate", "pending update"),
    DomainStatusCode("redemptionPeriod", "redemption period"),
    DomainStatusCode("renewPeriod", "renew period"),
    DomainStatusCode("serverDeleteProhibited", "server delete prohibited"),
    DomainStatusCode("serverHold", "server hold"),
    DomainStatusCode("serverRenewProhibited", "server renew prohibited"),
    DomainStatusCode("serverTransferProhibited", "server transfer prohibited"),
    DomainStatusCode("serverUpdateProhibited", "server update prohibited"),
    DomainStatusCode("transferPeriod", "transfer period"),
    DomainStatusCode("clientDeleteProhibited", "client delete prohibited"),
    DomainStatusCode("clientHold", "client hold"),
    DomainStatusCode("clientRenewProhibited", "client renew prohibited"),
    DomainStatusCode("clientTransferProhibited", "client transfer prohibited"),
    DomainStatusCode("clientUpdateProhibited", "client update prohibited"),
)

DEFAULT_DOMAIN_STATUS_REGISTRY = DomainStatusCodeRegistry(STANDARD_DOMAIN_STATUS_CODES)
