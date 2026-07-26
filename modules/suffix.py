"""
维护公共后缀（Public Suffix）信息
"""
from typing import Tuple

import httpx
import idna
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

class Suffix(BaseModel):
    name: str
    punycode: str | None = Field(None, description="Punycode 域名")
    idn: str | None = Field(None, description="IDN 域名")
    whois_server: str | None = Field(None, description="WHOIS 服务器")
    rdap_server: str | None = Field(None, description="RDAP 服务器")

    def resolve_idn(self):
        """
        解析 IDN 和 Punycode
        """
        self.idn, self.punycode = self.resolve_domain(self.name)
        return self

    def resolve_iana(self):
        """
        从 IANA 获取当前域名的 WHOIS 服务器和 RDAP 服务器
        """
        punycode = self.punycode or self.name

        url = f"https://www.iana.org/domains/root/db/{punycode}.html"
        try:
            response = httpx.get(url, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')

            whois_tag = soup.find('b', string=lambda s: s and 'WHOIS Server:' in s)
            if whois_tag and whois_tag.next_sibling:
                self.whois_server = whois_tag.next_sibling.strip()

            rdap_tag = soup.find('b', string=lambda s: s and 'RDAP Server:' in s)
            if rdap_tag and rdap_tag.next_sibling:
                self.rdap_server = rdap_tag.next_sibling.strip()
        except Exception:
            pass

        return self

    @staticmethod
    def resolve_domain(domain: str) -> Tuple[str|None, str|None]:
        """
        将域名标准化为 Punycode 和 IDN
        :param domain: 任意形式的域名
        :return: 标准 Punycode 与 IDN
        """
        domain = domain.strip().lower()
        try:
            idn_domain = idna.decode(domain)
            punycode_domain = idna.encode(idn_domain).decode('ascii')
        except idna.IDNAError:
            return None, None
        return idn_domain, punycode_domain
