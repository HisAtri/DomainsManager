from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import KeyValueWhoisParser
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


def create_sh_profile() -> WhoisProfile:
    return WhoisProfile(
        key="sh",
        suffixes=("sh",),
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(
            key="sh",
            version="1",
            not_found_markers=("Domain not found.",),
        ),
    )
