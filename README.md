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

from domainsmanager_lookup import DomainLookup, LookupOptions


async def main() -> None:
    lookup = DomainLookup()
    results = await lookup.lookup(["www.example.com"])
    result = results[0]

    if result.succeeded:
        print(result.identity)
        print(result.snapshot)
    else:
        print(result.error_code, result.error_message)


asyncio.run(main())
```

批量查询使用同一个方法，并按输入顺序逐项返回成功或失败：

```python
results = await lookup.lookup(
    ["example.com", "example.net"],
    options=LookupOptions(concurrency=10),
)
```

## 文档

- [架构与本次重构说明](docs/architecture.md)
- [ccTLD WHOIS Profile 扩展指南](docs/whois-profiles.md)
- [数据库缓存接入指南](docs/cache-backends.md)
- [数据库设计](docs/database-design.md)
- [后端 API 规范](docs/api/README.md)

## 测试

```powershell
uv run pytest -m "not network" -ra
```

`DomainLookup` 是业务层稳定入口，只公开域名标准化和批量查询两个方法。缓存、客户端、
解析器和原始响应属于包内实现；高级缓存适配器通过 `domainsmanager_lookup.spi` 接入。
旧 `modules.*` 导入路径暂时保留为兼容层。
