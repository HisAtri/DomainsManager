from functools import lru_cache

from domainsmanager_lookup._internal.whois_profiles.builtin import create_cn_profile
from domainsmanager_lookup._internal.whois_profiles.registry import WhoisProfileRegistry


def build_default_whois_registry() -> WhoisProfileRegistry:
    registry = WhoisProfileRegistry()
    registry.register(create_cn_profile())
    return registry


@lru_cache(maxsize=1)
def get_default_whois_registry() -> WhoisProfileRegistry:
    return build_default_whois_registry()
