from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import KeyValueWhoisParser
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


def create_in_profile() -> WhoisProfile:
    return WhoisProfile(
        key="in",
        suffixes=("in",),
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(
            key="in",
            version="1",
            not_found_markers=(" is available for registration",),
        ),
    )
