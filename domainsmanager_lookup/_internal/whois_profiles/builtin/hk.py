from datetime import UTC, datetime

from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


class HkWhoisParser(KeyValueWhoisParser):
    @staticmethod
    def _read_fields(body: str) -> dict[str, list[str]]:
        values = KeyValueWhoisParser._read_fields(body)
        in_nameservers = False
        awaiting_dnssec = False
        for line in body.splitlines():
            stripped = line.strip()
            if stripped == "DNSSEC:":
                awaiting_dnssec = True
                continue
            if awaiting_dnssec and stripped:
                values["dnssec"] = [stripped]
                awaiting_dnssec = False
                continue
            if stripped == "Name Servers Information:":
                in_nameservers = True
                continue
            if in_nameservers:
                if not stripped:
                    continue
                if stripped.endswith("Information:"):
                    break
                values.setdefault("name server", []).append(stripped)
        return values

    def parse_date(self, value: str | None) -> datetime | None:
        if value is not None:
            try:
                return datetime.strptime(value.strip(), "%d-%m-%Y").replace(tzinfo=UTC)
            except ValueError:
                pass
        return super().parse_date(value)


def create_hk_profile() -> WhoisProfile:
    return WhoisProfile(
        key="hk",
        suffixes=("hk",),
        query_strategy=StandardWhoisQuery(),
        parser=HkWhoisParser(
            key="hk",
            version="1",
            fields=WhoisFieldMap(
                domain=("Domain Name",),
                registrar=("Registrar Name",),
                status=("Domain Status",),
                registered_at=("Domain Name Commencement Date",),
                expires_at=("Expiry Date",),
                nameserver=("Name Server",),
                dnssec=("DNSSEC",),
            ),
            not_found_markers=("The domain has not been registered.",),
        ),
    )
