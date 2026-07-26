from threading import RLock
from types import MappingProxyType
from typing import Mapping

import idna

from modules.errors import UnsupportedWhoisProfileError
from modules.models.domain import NormalizedDomain
from modules.whois_profiles.base import WhoisProfile


class WhoisProfileRegistry:
    """线程安全、读路径 O(1) 的 WHOIS Profile 注册表。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._profiles: dict[str, WhoisProfile] = {}
        self._suffix_index: Mapping[str, WhoisProfile] = MappingProxyType({})
        self._generation = 0

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def register(self, profile: WhoisProfile, *, replace: bool = False) -> None:
        suffixes = tuple(self._normalize_suffix(value) for value in profile.suffixes)
        with self._lock:
            updated = dict(self._suffix_index)
            for suffix in suffixes:
                current = updated.get(suffix)
                if current is not None and current.key != profile.key:
                    raise ValueError(
                        f"suffix {suffix!r} 已由 Profile {current.key!r} 注册"
                    )

            if profile.key in self._profiles and not replace:
                raise ValueError(f"Profile {profile.key!r} 已存在")

            if replace and profile.key in self._profiles:
                old = self._profiles[profile.key]
                old_suffixes = {
                    self._normalize_suffix(value) for value in old.suffixes
                }
                for suffix in old_suffixes:
                    if updated.get(suffix) is old:
                        updated.pop(suffix, None)

            updated.update({suffix: profile for suffix in suffixes})
            self._profiles[profile.key] = profile
            self._suffix_index = MappingProxyType(updated)
            self._generation += 1

    def unregister(self, key: str) -> None:
        with self._lock:
            profile = self._profiles.pop(key, None)
            if profile is None:
                raise KeyError(key)
            updated = {
                suffix: item
                for suffix, item in self._suffix_index.items()
                if item is not profile
            }
            self._suffix_index = MappingProxyType(updated)
            self._generation += 1

    def resolve(self, domain: NormalizedDomain) -> WhoisProfile:
        index = self._suffix_index
        profile = index.get(domain.public_suffix) or index.get(domain.tld)
        if profile is None:
            raise UnsupportedWhoisProfileError(
                f"未配置 {domain.public_suffix!r} 的 WHOIS Profile"
            )
        return profile

    def get(self, suffix: str) -> WhoisProfile | None:
        return self._suffix_index.get(self._normalize_suffix(suffix))

    def profiles(self) -> tuple[WhoisProfile, ...]:
        with self._lock:
            return tuple(self._profiles.values())

    @staticmethod
    def _normalize_suffix(suffix: str) -> str:
        candidate = suffix.strip().lower().lstrip(".").rstrip(".")
        if not candidate:
            raise ValueError("suffix 不能为空")
        try:
            return idna.encode(candidate, uts46=True).decode("ascii")
        except idna.IDNAError as exc:
            raise ValueError(f"无效 suffix：{suffix!r}") from exc
