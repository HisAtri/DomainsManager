# DomainsManager

用于集中管理多个域名的状态、生命周期、注册商、名称服务器和 DNSSEC 等信息。

项目当前实现了以下基础能力：

- IDNA/Punycode 域名标准化与 Public Suffix 提取；
- IANA Root Database WHOIS 端点发现；
- IANA RDAP Bootstrap 端点发现；
- 原始 RDAP/WHOIS 报文的缓存抽象；
- RDAP 优先、WHOIS 回退的查询编排；
- 可按 ccTLD 扩展的 WHOIS Profile 框架；
- 受限并发的批量域名查询。

## 快速使用

```python
import asyncio

from modules.services import DomainLookupService


async def main() -> None:
    service = DomainLookupService()
    result = await service.lookup("www.example.com")

    print(result.domain)
    print(result.info)
    print(result.response_cache_hit)


asyncio.run(main())
```

批量查询：

```python
results = await service.lookup_many(
    ["example.com", "example.net"],
    concurrency=10,
)
```

## 文档

- [架构与本次重构说明](docs/architecture.md)
- [ccTLD WHOIS Profile 扩展指南](docs/whois-profiles.md)
- [数据库缓存接入指南](docs/cache-backends.md)

## 测试

```powershell
.venv\Scripts\python.exe -m unittest discover -v
```

当前缓存默认使用内存实现。后续接入数据库时，实现 `DomainResponseCache` 与
`RegistryEndpointCache` 并注入 `DomainLookupService` 即可。
