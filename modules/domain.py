import socket
import idna
import uuid

import requests
from bs4 import BeautifulSoup


class Domain:
    def __init__(self, domain_name: str):
        # 主要数据，储存于 domains 表
        self.name = domain_name.lower()
        self.whois_server: str = None
        self.uuid: str = None
        self.active: bool = True
        # 常量，自动生成，不可更改
        self.domain: str = None
        self.idn_domain: str = None
        self.tld: str = None
        self.idn_tld: str = None
        # 可变数据，储存于 datas 表
        self.reg_time: int = None
        self.upd_time: int = None
        self.exp_time: int = None
        self.datas: dict = dict()
        # 初始化
        self.get_tld()
        self.get_whois_server()
        self.uuid: str = uuid.uuid5(uuid.NAMESPACE_DNS, self.domain).hex


    def __str__(self):
        return self.name

    def query_whois(self) -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((self.whois_server, 43))
        s.send((self.domain + "\r\n").encode())
        response = b""
        while True:
            data = s.recv(4096)
            if not data:
                break
            response += data
        s.close()
        return response.decode()

    def get_tld(self):
        _domain = self.name
        idn_domain = idna.encode(_domain).decode('utf-8')
        punycode_domain = idn_domain

        idn_parts = _domain.split('.')
        idn_tld = idn_parts[-1]
        punycode_parts = punycode_domain.split('.')
        punycode_tld = punycode_parts[-1]

        # Punycode: 纯ASCII字符
        # IDN: 国际化域名
        # 内部逻辑默认使用前者
        self.domain = punycode_domain
        self.tld = punycode_tld
        self.idn_domain = idn_domain
        self.idn_tld = idn_tld

    def get_whois_server(self):
        url = f"https://www.iana.org/domains/root/db/{self.tld}.html"
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        whois_server_tag = soup.find("b", string="WHOIS Server:")
        if whois_server_tag:
            self.whois_server = whois_server_tag.next_sibling.strip()
        else:
            print(f"WHOIS Server information not found for TLD {self.tld}")

    def update(self, data: dict):
        for key, value in data.items():
            setattr(self, key, value)

    def dump(self) -> dict:
        return {
            "name": self.name,
            "whois_server": self.whois_server,
            "uuid": self.uuid,
            "active": self.active,
            "domain": self.domain,
            "idn_domain": self.idn_domain,
            "tld": self.tld,
            "idn_tld": self.idn_tld,
            "reg_time": self.reg_time,
            "exp_time": self.exp_time
        }

    def parse_whois(self, whois_data: str):
        """
        解析WHOIS数据
        :param whois_data:
        :return:
        """

if __name__ == "__main__":
    domain = Domain("原神.org")
    print(domain.query_whois())
