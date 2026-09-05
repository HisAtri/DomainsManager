from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import KeyValueWhoisParser
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


def create_do_profile() -> WhoisProfile:
    return WhoisProfile(
        key="do",
        suffixes=("do",),
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(
            key="do",
            version="1",
            not_found_markers=("The queried object does not exist: No Object Found",),
        ),
    )
