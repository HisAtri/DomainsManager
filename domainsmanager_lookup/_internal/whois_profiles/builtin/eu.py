from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.key_value import (
    KeyValueWhoisParser,
    WhoisFieldMap,
)
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery


class EuWhoisParser(KeyValueWhoisParser):
    @staticmethod
    def _read_fields(body: str) -> dict[str, list[str]]:
        values = KeyValueWhoisParser._read_fields(body)
        section = ""
        for line in body.splitlines():
            stripped = line.strip()
            if stripped in {"Registrar:", "Name servers:"}:
                section = stripped[:-1]
                continue
            if not stripped:
                section = ""
                continue
            if section == "Registrar" and stripped.startswith("Name:"):
                values["registrar"] = [stripped.split(":", 1)[1].strip()]
            elif section == "Name servers:" or section == "Name servers":
                values.setdefault("name server", []).append(stripped)
        return values


def create_eu_profile() -> WhoisProfile:
    return WhoisProfile(
        key="eu",
        suffixes=("eu",),
        query_strategy=StandardWhoisQuery(),
        parser=EuWhoisParser(
            key="eu",
            version="1",
            fields=WhoisFieldMap(
                domain=("Domain",),
                registrar=("Registrar",),
                nameserver=("Name Server",),
            ),
            not_found_markers=("Status: AVAILABLE",),
        ),
    )
