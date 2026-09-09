from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


class ItWhoisParser(KeyValueWhoisParser):
    @staticmethod
    def _read_fields(body: str) -> dict[str, list[str]]:
        values = KeyValueWhoisParser._read_fields(body)
        section = ""
        for line in body.splitlines():
            stripped = line.strip()
            if stripped in {
                "Registrant",
                "Admin Contact",
                "Technical Contacts",
                "Registrar",
                "Nameservers",
            }:
                section = stripped
                continue
            if not stripped:
                section = ""
                continue
            if section == "Registrar" and stripped.startswith("Organization:"):
                values["registrar"] = [stripped.split(":", 1)[1].strip()]
            elif section == "Registrar" and stripped.startswith("Web:"):
                values["registrar url"] = [stripped.split(":", 1)[1].strip()]
            elif section == "Nameservers":
                values.setdefault("name server", []).append(stripped)
        return values


def create_it_profile() -> WhoisProfile:
    return WhoisProfile(
        key="it",
        suffixes=("it",),
        query_strategy=StandardWhoisQuery(),
        parser=ItWhoisParser(
            key="it",
            version="1",
            fields=WhoisFieldMap(
                domain=("Domain",),
                registrar=("Registrar",),
                registrar_url=("Registrar URL",),
                status=("Status",),
                registered_at=("Created",),
                expires_at=("Expire Date",),
                updated_at=("Last Update",),
                nameserver=("Name Server",),
                dnssec=("Signed",),
            ),
            not_found_markers=("Status:             AVAILABLE",),
        ),
    )
