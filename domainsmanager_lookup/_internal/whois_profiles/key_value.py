import re
from dataclasses import dataclass
from datetime import datetime

from domainsmanager_lookup._internal.models.domain import (
    DNSSECInfo,
    DomainDates,
    DomainInfo,
    NormalizedDomain,
    RegistrarInfo,
)
from domainsmanager_lookup._internal.models.response import RawLookupResponse
from domainsmanager_lookup._internal.whois_profiles.base import WhoisResponseParser
from domainsmanager_lookup._internal.whois_profiles.models import WhoisParseResult, WhoisResponseStatus


@dataclass(frozen=True, slots=True)
class WhoisFieldMap:
    domain: tuple[str, ...] = ("Domain Name",)
    handle: tuple[str, ...] = ("Registry Domain ID",)
    registrar: tuple[str, ...] = ("Registrar",)
    registrar_id: tuple[str, ...] = ("Registrar IANA ID",)
    registrar_url: tuple[str, ...] = ("Registrar URL",)
    abuse_email: tuple[str, ...] = ("Registrar Abuse Contact Email",)
    abuse_phone: tuple[str, ...] = ("Registrar Abuse Contact Phone",)
    status: tuple[str, ...] = ("Domain Status", "Status")
    registered_at: tuple[str, ...] = (
        "Creation Date",
        "Created On",
        "Registered On",
    )
    expires_at: tuple[str, ...] = (
        "Registry Expiry Date",
        "Expiration Date",
        "Expiry Date",
    )
    updated_at: tuple[str, ...] = ("Updated Date", "Last Updated")
    nameserver: tuple[str, ...] = ("Name Server", "Nserver")
    dnssec: tuple[str, ...] = ("DNSSEC",)


class KeyValueWhoisParser(WhoisResponseParser):
    """可配置的 ``Key: Value`` WHOIS 解析器。"""

    def __init__(
        self,
        *,
        key: str,
        version: str,
        fields: WhoisFieldMap | None = None,
        not_found_markers: tuple[str, ...] = (),
        rate_limit_markers: tuple[str, ...] = (),
        access_denied_markers: tuple[str, ...] = (),
        temporary_failure_markers: tuple[str, ...] = (),
    ) -> None:
        self.key = key
        self.version = version
        self._fields = fields or WhoisFieldMap()
        self._markers = {
            WhoisResponseStatus.NOT_FOUND: not_found_markers,
            WhoisResponseStatus.RATE_LIMITED: rate_limit_markers,
            WhoisResponseStatus.ACCESS_DENIED: access_denied_markers,
            WhoisResponseStatus.TEMPORARY_FAILURE: temporary_failure_markers,
        }

    def parse(
        self,
        response: RawLookupResponse,
        domain: NormalizedDomain,
    ) -> WhoisParseResult:
        classified = self.classify(response.body)
        if classified is not WhoisResponseStatus.FOUND:
            return WhoisParseResult(
                status=classified,
                parser_key=self.key,
                parser_version=self.version,
            )

        values = self._read_fields(response.body)
        field = self._fields
        known_labels = {
            label.casefold()
            for labels in (
                field.domain,
                field.handle,
                field.registrar,
                field.registrar_id,
                field.registrar_url,
                field.abuse_email,
                field.abuse_phone,
                field.status,
                field.registered_at,
                field.expires_at,
                field.updated_at,
                field.nameserver,
                field.dnssec,
            )
            for label in labels
        }
        if not known_labels.intersection(values):
            return WhoisParseResult(
                status=WhoisResponseStatus.UNKNOWN,
                warnings=["响应中没有任何已配置字段，注册局格式可能已经变化"],
                parser_key=self.key,
                parser_version=self.version,
            )

        domain_name = self._first(values, field.domain)
        warnings: list[str] = []
        if domain_name is None:
            warnings.append("响应中未找到域名字段，使用请求域名")

        raw_registrar_id = self._first(values, field.registrar_id)
        try:
            registrar_id = int(raw_registrar_id) if raw_registrar_id else None
        except ValueError:
            registrar_id = None
            warnings.append(f"无法解析注册商 IANA ID：{raw_registrar_id!r}")

        registrar_values = (
            self._first(values, field.registrar),
            self._first(values, field.registrar_url),
            self._first(values, field.abuse_email),
            self._first(values, field.abuse_phone),
        )
        registrar = None
        if registrar_id is not None or any(registrar_values):
            registrar = RegistrarInfo(
                name=registrar_values[0],
                iana_id=registrar_id,
                url=registrar_values[1],
                abuse_email=registrar_values[2],
                abuse_phone=registrar_values[3],
            )

        registered_raw = self._first(values, field.registered_at)
        expires_raw = self._first(values, field.expires_at)
        updated_raw = self._first(values, field.updated_at)
        registered_at = self.parse_date(registered_raw)
        expires_at = self.parse_date(expires_raw)
        updated_at = self.parse_date(updated_raw)
        for label, raw, parsed in (
            ("注册时间", registered_raw, registered_at),
            ("过期时间", expires_raw, expires_at),
            ("更新时间", updated_raw, updated_at),
        ):
            if raw is not None and parsed is None:
                warnings.append(f"无法解析{label}：{raw!r}")

        dnssec = self._first(values, field.dnssec)
        return WhoisParseResult(
            status=WhoisResponseStatus.FOUND,
            info=DomainInfo(
                domain=(domain_name or domain.registrable_domain).lower(),
                registry_handle=self._first(values, field.handle),
                registrar=registrar,
                statuses=[
                    item.split()[0]
                    for item in self._all(values, field.status)
                    if item.strip()
                ],
                dates=DomainDates(
                    registered_at=registered_at,
                    expires_at=expires_at,
                    updated_at=updated_at,
                ),
                nameservers=sorted(
                    {
                        item.split()[0].lower().rstrip(".")
                        for item in self._all(values, field.nameserver)
                        if item.strip()
                    }
                ),
                dnssec=DNSSECInfo(enabled=self.parse_dnssec(dnssec)),
                source="whois",
                source_url=response.endpoint,
                fetched_at=response.fetched_at,
                parser_version=f"{self.key}:{self.version}",
            ),
            warnings=warnings,
            parser_key=self.key,
            parser_version=self.version,
        )

    def classify(self, body: str) -> WhoisResponseStatus:
        folded = body.casefold()
        for status, markers in self._markers.items():
            if any(marker.casefold() in folded for marker in markers):
                return status
        if not body.strip():
            return WhoisResponseStatus.TEMPORARY_FAILURE
        return WhoisResponseStatus.FOUND

    def parse_date(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        candidate = value.strip().rstrip(".")
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            pass
        for pattern in ("%Y-%m-%d", "%d-%b-%Y", "%Y.%m.%d"):
            try:
                return datetime.strptime(candidate, pattern)
            except ValueError:
                continue
        return None

    @staticmethod
    def parse_dnssec(value: str | None) -> bool | None:
        if value is None:
            return None
        normalized = value.casefold().strip()
        if normalized in {"signed", "yes", "true", "active"}:
            return True
        if normalized in {"unsigned", "no", "false", "inactive"}:
            return False
        return None

    @staticmethod
    def _read_fields(body: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for line in body.splitlines():
            match = re.match(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$", line)
            if match:
                result.setdefault(match.group(1).casefold(), []).append(match.group(2))
        return result

    @staticmethod
    def _first(
        values: dict[str, list[str]],
        labels: tuple[str, ...],
    ) -> str | None:
        matches = KeyValueWhoisParser._all(values, labels)
        return matches[0] if matches else None

    @staticmethod
    def _all(
        values: dict[str, list[str]],
        labels: tuple[str, ...],
    ) -> list[str]:
        for label in labels:
            matches = values.get(label.casefold())
            if matches:
                return matches
        return []
