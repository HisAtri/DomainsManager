from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import KeyValueWhoisParser
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


def create_cc_profile() -> WhoisProfile:
    return WhoisProfile(
        key="cc",
        suffixes=("cc",),
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(
            key="cc",
            version="1",
            not_found_markers=("No match for",),
        ),
    )
