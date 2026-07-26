import socket

from abc import ABC, abstractmethod


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

class Whois(ABC):
    """
    WHOIS报文解析基类；子类需要按照对应后缀 WHOIS 报文的格式，解码出对应信息
    """
    ...

