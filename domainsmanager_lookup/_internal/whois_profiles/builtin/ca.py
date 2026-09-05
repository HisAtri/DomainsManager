from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import KeyValueWhoisParser
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


def create_ca_profile() -> WhoisProfile:
    return WhoisProfile(
        key="ca",
        suffixes=("ca",),
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(
            key="ca",
            version="1",
            not_found_markers=("Not found:",),
        ),
    )
