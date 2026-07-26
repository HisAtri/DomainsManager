from dataclasses import dataclass

from modules.models.domain import NormalizedDomain
from modules.whois_profiles.base import WhoisQueryStrategy


@dataclass(frozen=True, slots=True)
class StandardWhoisQuery(WhoisQueryStrategy):
    """适用于发送 ASCII 可注册域名并以 CRLF 结尾的注册局。"""

    template: str = "{domain}\r\n"
    response_encodings: tuple[str, ...] = ("utf-8", "latin-1")

    def build_query(self, domain: NormalizedDomain) -> bytes:
        value = self.template.format(
            domain=domain.registrable_domain,
            ascii_name=domain.ascii_name,
            suffix=domain.public_suffix,
            tld=domain.tld,
        )
        return value.encode("ascii")

    def decode(self, response: bytes) -> str:
        for encoding in self.response_encodings:
            try:
                return response.decode(encoding)
            except UnicodeDecodeError:
                continue
        return response.decode(self.response_encodings[0], errors="replace")
