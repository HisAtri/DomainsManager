import re
from datetime import datetime

from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


class TwWhoisParser(KeyValueWhoisParser):
    @staticmethod
    def _read_fields(body: str) -> dict[str, list[str]]:
        values = KeyValueWhoisParser._read_fields(body)
        in_nameservers = False
        for line in body.splitlines():
            stripped = line.strip()
            if stripped == "Domain servers in listed order:":
                in_nameservers = True
                continue
            if in_nameservers:
                if not stripped:
                    continue
                if ":" in stripped:
                    in_nameservers = False
                else:
                    values.setdefault("name server", []).append(stripped)
                    continue
            for label in ("Record expires on", "Record created on"):
                if stripped.startswith(f"{label} "):
                    values.setdefault(label.casefold(), []).append(
                        stripped[len(label) :].strip()
                    )
        return values

    def parse_date(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        match = re.fullmatch(r"(.+) \(UTC([+-]\d+)\)", value.strip())
        if match:
            try:
                offset = int(match.group(2))
                return datetime.fromisoformat(f"{match.group(1)}{offset:+03d}:00")
            except ValueError:
                return None
        return super().parse_date(value)


def create_tw_profile() -> WhoisProfile:
    return WhoisProfile(
        key="tw",
        suffixes=("tw",),
        query_strategy=StandardWhoisQuery(),
        parser=TwWhoisParser(
            key="tw",
            version="1",
            fields=WhoisFieldMap(
                domain=("Domain Name",),
                registrar=("Registration Service Provider",),
                registrar_url=("Registration Service URL",),
                status=("Domain Status",),
                registered_at=("Record created on",),
                expires_at=("Record expires on",),
                nameserver=("Name Server",),
            ),
            not_found_markers=("No Found",),
        ),
    )
