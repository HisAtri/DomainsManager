from datetime import UTC, datetime

from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


class PlWhoisParser(KeyValueWhoisParser):
    @staticmethod
    def _read_fields(body: str) -> dict[str, list[str]]:
        values = KeyValueWhoisParser._read_fields(body)
        section = ""
        for line in body.splitlines():
            stripped = line.strip()
            if stripped == "REGISTRAR:":
                section = "registrar"
                continue
            if section == "registrar" and stripped:
                values["registrar"] = [stripped]
                section = ""
            elif line[:1].isspace() and stripped and ":" not in stripped:
                values.setdefault("nameservers", []).append(stripped)
        return values

    def parse_date(self, value: str | None) -> datetime | None:
        if value is not None:
            try:
                return datetime.strptime(value.strip(), "%Y.%m.%d %H:%M:%S").replace(
                    tzinfo=UTC
                )
            except ValueError:
                pass
        return super().parse_date(value)


def create_pl_profile() -> WhoisProfile:
    return WhoisProfile(
        key="pl",
        suffixes=("pl",),
        query_strategy=StandardWhoisQuery(),
        parser=PlWhoisParser(
            key="pl",
            version="1",
            fields=WhoisFieldMap(
                domain=("DOMAIN NAME",),
                registrar=("REGISTRAR",),
                registered_at=("created",),
                expires_at=("renewal date",),
                updated_at=("last modified",),
                nameserver=("nameservers",),
                dnssec=("dnssec",),
            ),
            not_found_markers=("No information available about domain name",),
        ),
    )
