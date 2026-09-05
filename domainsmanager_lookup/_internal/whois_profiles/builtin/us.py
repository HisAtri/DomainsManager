from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import KeyValueWhoisParser
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


def create_us_profile() -> WhoisProfile:
    return WhoisProfile(
        key="us",
        suffixes=("us",),
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(
            key="us",
            version="1",
            not_found_markers=("No Data Found",),
        ),
    )
