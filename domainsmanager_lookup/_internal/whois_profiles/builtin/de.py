from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


def create_de_profile() -> WhoisProfile:
    return WhoisProfile(
        key="de",
        suffixes=("de",),
        query_strategy=StandardWhoisQuery(template="-T dn,ace {domain}\r\n"),
        parser=KeyValueWhoisParser(
            key="de",
            version="1",
            fields=WhoisFieldMap(
                domain=("Domain",),
                status=("Status",),
                updated_at=("Changed",),
                nameserver=("Nserver",),
            ),
            not_found_markers=("Status: free",),
        ),
    )
