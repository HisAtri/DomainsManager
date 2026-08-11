class DomainManagerError(Exception):
    """域名管理模块的基础异常。"""


class DomainNormalizationError(DomainManagerError, ValueError):
    """域名无法完成 IDNA 或 Public Suffix 标准化。"""


class EndpointDiscoveryError(DomainManagerError):
    """无法发现域名注册局的查询端点。"""


class ProtocolUnavailableError(DomainManagerError):
    """注册局未提供指定协议的查询端点。"""


class LookupFailedError(DomainManagerError):
    """所有域名信息查询方式均失败。"""


class ResponseParseError(DomainManagerError, ValueError):
    """无法把注册局响应解析为统一模型。"""


class UnsupportedWhoisProfileError(DomainManagerError, LookupError):
    """没有为指定域名空间注册 WHOIS Profile。"""


class WhoisResponseError(DomainManagerError):
    """WHOIS 返回未注册、限流或拒绝访问等非成功状态。"""
