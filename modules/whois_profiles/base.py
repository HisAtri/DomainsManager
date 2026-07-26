from abc import ABC, abstractmethod
from dataclasses import dataclass

from modules.models.domain import NormalizedDomain
from modules.models.response import RawLookupResponse
from modules.whois_profiles.models import WhoisParseResult


class WhoisQueryStrategy(ABC):
    """描述某个注册局的查询内容和响应解码方式。"""

    @abstractmethod
    def build_query(self, domain: NormalizedDomain) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def decode(self, response: bytes) -> str:
        raise NotImplementedError


class WhoisResponseParser(ABC):
    key: str
    version: str

    @abstractmethod
    def parse(
        self,
        response: RawLookupResponse,
        domain: NormalizedDomain,
    ) -> WhoisParseResult:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class WhoisProfile:
    """一个域名空间完整的 WHOIS 查询和解析配置。"""

    key: str
    suffixes: tuple[str, ...]
    query_strategy: WhoisQueryStrategy
    parser: WhoisResponseParser

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("WHOIS Profile key 不能为空")
        if not self.suffixes:
            raise ValueError("WHOIS Profile 至少需要声明一个 suffix")
