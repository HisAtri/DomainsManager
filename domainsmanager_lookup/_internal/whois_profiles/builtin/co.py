from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import KeyValueWhoisParser
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


class CoWhoisParser(KeyValueWhoisParser):
    @staticmethod
    def parse_dnssec(value: str | None) -> bool | None:
        if value is not None and value.casefold().strip() == "signeddelegation":
            return True
        return KeyValueWhoisParser.parse_dnssec(value)


def create_co_profile() -> WhoisProfile:
    return WhoisProfile(
        key="co",
        suffixes=("co",),
        query_strategy=StandardWhoisQuery(),
        parser=CoWhoisParser(
            key="co",
            version="1",
            not_found_markers=("The queried object does not exist: DOMAIN NOT FOUND",),
        ),
    )
