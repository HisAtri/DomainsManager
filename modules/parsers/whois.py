import re
from datetime import datetime

from modules.errors import ResponseParseError, WhoisResponseError
from modules.models.domain import (
    DNSSECInfo,
    DomainDates,
    DomainInfo,
    NormalizedDomain,
    RegistrarInfo,
)
from modules.models.response import RawLookupResponse
from modules.whois_profiles.defaults import get_default_whois_registry
from modules.whois_profiles.models import WhoisParseResult, WhoisResponseStatus
from modules.whois_profiles.registry import WhoisProfileRegistry


class ProfiledWhoisParser:
    """根据 Public Suffix 从注册表选择精确解析器。"""

    def __init__(self, registry: WhoisProfileRegistry | None = None) -> None:
        self._registry = registry or get_default_whois_registry()

    def parse_result(
        self,
        response: RawLookupResponse,
        domain: NormalizedDomain,
    ) -> WhoisParseResult:
        profile = self._registry.resolve(domain)
        return profile.parser.parse(response, domain)

    def parse(
        self,
        response: RawLookupResponse,
        domain: NormalizedDomain,
    ) -> DomainInfo:
        result = self.parse_result(response, domain)
        if result.status is not WhoisResponseStatus.FOUND or result.info is None:
            raise WhoisResponseError(
                f"WHOIS Profile {result.parser_key!r} 返回状态 {result.status.value!r}"
            )
        return result.info


class WhoisParser:
    """旧版通用解析器；新代码应使用 ProfiledWhoisParser。"""

    VERSION = "1"

    def parse(
        self,
        response: RawLookupResponse,
        domain: NormalizedDomain,
    ) -> DomainInfo:
        if not response.body.strip():
            raise ResponseParseError("WHOIS 响应为空")

        registrar_id = self._first(response.body, r"Registrar IANA ID:\s*(.+)")
        try:
            iana_id = int(registrar_id) if registrar_id else None
        except ValueError:
            iana_id = None

        dnssec = self._first(response.body, r"DNSSEC:\s*(.+)")
        domain_name = self._first(response.body, r"Domain Name:\s*(.+)")
        return DomainInfo(
            domain=(domain_name or domain.registrable_domain).lower(),
            registry_handle=self._first(
                response.body,
                r"Registry Domain ID:\s*(.+)",
            ),
            registrar=RegistrarInfo(
                name=self._first(response.body, r"Registrar:\s*(.+)"),
                iana_id=iana_id,
                url=self._first(response.body, r"Registrar URL:\s*(.+)"),
                abuse_email=self._first(
                    response.body,
                    r"Registrar Abuse Contact Email:\s*(.+)",
                ),
                abuse_phone=self._first(
                    response.body,
                    r"Registrar Abuse Contact Phone:\s*(.+)",
                ),
            ),
            statuses=self._all(response.body, r"Domain Status:\s*(\S+)"),
            dates=DomainDates(
                registered_at=self._date(
                    self._first(
                        response.body,
                        r"(?:Creation Date|Created On|Registered On):\s*(.+)",
                    )
                ),
                expires_at=self._date(
                    self._first(
                        response.body,
                        r"(?:Registry Expiry Date|Expiration Date|Expiry Date):\s*(.+)",
                    )
                ),
                updated_at=self._date(
                    self._first(response.body, r"Updated Date:\s*(.+)")
                ),
            ),
            nameservers=sorted(
                {
                    value.lower().rstrip(".")
                    for value in self._all(response.body, r"Name Server:\s*(\S+)")
                }
            ),
            dnssec=DNSSECInfo(
                enabled=None
                if dnssec is None
                else dnssec.lower() not in {"unsigned", "no", "false", "inactive"}
            ),
            source="whois",
            source_url=response.endpoint,
            fetched_at=response.fetched_at,
            parser_version=self.VERSION,
        )

    @staticmethod
    def _first(text: str, pattern: str) -> str | None:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        return match.group(1).strip() if match else None

    @staticmethod
    def _all(text: str, pattern: str) -> list[str]:
        return [
            value.strip()
            for value in re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        ]

    @staticmethod
    def _date(value: str | None) -> datetime | None:
        if value is None:
            return None
        candidate = value.strip().rstrip(".")
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return None
