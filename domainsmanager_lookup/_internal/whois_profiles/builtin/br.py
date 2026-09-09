import re
from datetime import UTC, datetime

from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


class BrWhoisParser(KeyValueWhoisParser):
    def parse_date(self, value: str | None) -> datetime | None:
        if value is not None:
            match = re.match(r"(\d{8})", value.strip())
            if match:
                try:
                    return datetime.strptime(match.group(1), "%Y%m%d").replace(
                        tzinfo=UTC
                    )
                except ValueError:
                    return None
        return super().parse_date(value)


def create_br_profile() -> WhoisProfile:
    return WhoisProfile(
        key="br",
        suffixes=("br",),
        query_strategy=StandardWhoisQuery(),
        parser=BrWhoisParser(
            key="br",
            version="1",
            fields=WhoisFieldMap(
                domain=("domain",),
                status=("status",),
                registered_at=("created",),
                expires_at=("expires",),
                updated_at=("changed",),
                nameserver=("nserver",),
            ),
            not_found_markers=("% No match for",),
        ),
    )
