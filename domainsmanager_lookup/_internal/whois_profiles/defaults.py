from functools import lru_cache

from domainsmanager_lookup._internal.whois_profiles.builtin import (
    create_ca_profile,
    create_cc_profile,
    create_cn_profile,
    create_co_profile,
    create_do_profile,
    create_eu_profile,
    create_fr_profile,
    create_hk_profile,
    create_sh_profile,
    create_tw_profile,
    create_us_profile,
)
from domainsmanager_lookup._internal.whois_profiles.registry import WhoisProfileRegistry


def build_default_whois_registry() -> WhoisProfileRegistry:
    registry = WhoisProfileRegistry()
    registry.register(create_cn_profile())
    for factory in (
        create_us_profile,
        create_co_profile,
        create_cc_profile,
        create_ca_profile,
        create_do_profile,
        create_eu_profile,
        create_fr_profile,
        create_hk_profile,
        create_tw_profile,
        create_sh_profile,
    ):
        registry.register(factory())
    return registry


@lru_cache(maxsize=1)
def get_default_whois_registry() -> WhoisProfileRegistry:
    return build_default_whois_registry()
