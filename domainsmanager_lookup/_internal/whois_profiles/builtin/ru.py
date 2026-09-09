from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


class RuWhoisParser(KeyValueWhoisParser):
    @staticmethod
    def _read_fields(body: str) -> dict[str, list[str]]:
        values = KeyValueWhoisParser._read_fields(body)
        states = values.get("state")
        if states:
            values["state"] = [
                state.strip()
                for value in states
                for state in value.split(",")
                if state.strip()
            ]
        return values


def create_ru_profile() -> WhoisProfile:
    return WhoisProfile(
        key="ru",
        suffixes=("ru",),
        query_strategy=StandardWhoisQuery(),
        parser=RuWhoisParser(
            key="ru",
            version="1",
            fields=WhoisFieldMap(
                domain=("domain",),
                registrar=("registrar",),
                status=("state",),
                registered_at=("created",),
                expires_at=("paid-till",),
                nameserver=("nserver",),
            ),
            not_found_markers=("No entries found for the selected source(s).",),
        ),
    )
