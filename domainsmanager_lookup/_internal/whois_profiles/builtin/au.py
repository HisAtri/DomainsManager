from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


def create_au_profile() -> WhoisProfile:
    return WhoisProfile(
        key="au",
        suffixes=("au",),
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(
            key="au",
            version="1",
            fields=WhoisFieldMap(
                domain=("Domain Name",),
                handle=("Registry Domain ID",),
                registrar=("Registrar Name",),
                registrar_url=("Registrar URL",),
                abuse_email=("Registrar Abuse Contact Email",),
                abuse_phone=("Registrar Abuse Contact Phone",),
                status=("Status",),
                updated_at=("Last Modified",),
                nameserver=("Name Server",),
                dnssec=("DNSSEC",),
            ),
            not_found_markers=("Domain not found.",),
        ),
    )
