from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


class NlWhoisParser(KeyValueWhoisParser):
    @staticmethod
    def _read_fields(body: str) -> dict[str, list[str]]:
        values = KeyValueWhoisParser._read_fields(body)
        section = ""
        for line in body.splitlines():
            stripped = line.strip()
            if stripped in {"Registrar:", "Domain nameservers:"}:
                section = stripped[:-1]
                continue
            if not stripped:
                section = ""
                continue
            if section == "Registrar":
                values["registrar"] = [stripped]
                section = ""
            elif section == "Domain nameservers":
                values.setdefault("name server", []).append(stripped)
        return values


def create_nl_profile() -> WhoisProfile:
    return WhoisProfile(
        key="nl",
        suffixes=("nl",),
        query_strategy=StandardWhoisQuery(),
        parser=NlWhoisParser(
            key="nl",
            version="1",
            fields=WhoisFieldMap(
                domain=("Domain name",),
                registrar=("Registrar",),
                status=("Status",),
                registered_at=("Creation Date",),
                updated_at=("Updated Date",),
                nameserver=("Name Server",),
                dnssec=("DNSSEC",),
            ),
            not_found_markers=(" is free",),
        ),
    )
