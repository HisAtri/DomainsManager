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

## FastAPI 服务器（阶段性）

当前已提供 FastAPI 应用骨架、健康检查和本地用户认证接口。域名及管理员业务路由将按
[后端 API 规范](docs/api/README.md) 逐步实现。开发环境启动：

```powershell
uv sync --extra api
$env:DOMAINSMANAGER_DATABASE_URL = "postgresql+asyncpg://user:password@localhost/domainsmanager"
$env:DOMAINSMANAGER_JWT_SECRET_KEY = "replace-me"
$env:DOMAINSMANAGER_REFRESH_TOKEN_PEPPER = "replace-me"
uv run alembic upgrade head
uv run domainsmanager-api
```

服务默认监听 `http://127.0.0.1:7920`，健康检查为 `/health/live` 和
`/health/ready`。数据库迁移必须在服务启动前单独执行，不会由应用自动运行。首次部署可临时设置
`DOMAINSMANAGER_BOOTSTRAP_ADMIN_USERNAME` 和
`DOMAINSMANAGER_BOOTSTRAP_ADMIN_PASSWORD`；只有数据库没有任何用户时才会创建管理员，后续启动
不会用这些变量修改或新增账号。

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
