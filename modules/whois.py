import socket
import idna

import requests
from bs4 import BeautifulSoup


def query_whois(domain: str, whois_server: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((whois_server, 43))
    s.send((domain + "\r\n").encode())

    response = b""
    while True:
        data = s.recv(4096)
        if not data:
            break
        response += data

    s.close()
    return response.decode()


def get_whois_server(tld: str) -> str:
    url = f"https://www.iana.org/domains/root/db/{tld}.html"
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to retrieve data for TLD {tld}")
    soup = BeautifulSoup(response.text, 'html.parser')
    whois_server_tag = soup.find("b", string="WHOIS Server:")

    if whois_server_tag:
        whois_server = whois_server_tag.next_sibling.strip()
        return whois_server
    else:
        raise Exception(f"WHOIS Server information not found for TLD {tld}")


def get_tld(domain: str) -> str|None:
    try:
        # 尝试将域名解码为IDNA格式
        _domain = idna.encode(domain).decode('utf-8')
    except idna.IDNAError:
        return None

    return _domain.split('.')[-1]

if __name__ == "__main__":
    print(get_tld("app.中国"))
