from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


def create_fr_profile():
    return WhoisProfile(
        key="fr",
        suffixes=("fr",),
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(
            key="fr",
            version="1",
            fields=WhoisFieldMap(
                domain=("domain",),
                handle=("nic-hdl",),
                registrar=("registrar",),
                status=("eppstatus", "status"),
                registered_at=("created",),
                expires_at=("Expiry Date",),
                updated_at=("last-update", "changed"),
                nameserver=("nserver",),
            ),
            not_found_markers=("%% NOT FOUND",),
        ),
    )
