from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import KeyValueWhoisParser, WhoisFieldMap
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


def create_cn_profile() -> WhoisProfile:
    return WhoisProfile(
        key="cn",
        suffixes=("cn", "公司.cn", "网络.cn"),
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(
            key="cn",
            version="1",
            fields=WhoisFieldMap(
                domain=("Domain Name",),
                handle=("ROID",),
                registrar=("Sponsoring Registrar",),
                registered_at=("Registration Time",),
                expires_at=("Expiration Time",),
                nameserver=("Name Server",),
                dnssec=("DNSSEC",),
            ),
            not_found_markers=("No matching record",),
            rate_limit_markers=("query rate limit exceeded",),
            access_denied_markers=("access denied",),
        ),
    )
