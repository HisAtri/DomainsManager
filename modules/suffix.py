"""注册局端点模型兼容入口。"""

from modules.clients.iana import IanaClient
from modules.models.registry import RegistryEndpoint

__all__ = ["IanaClient", "RegistryEndpoint"]
