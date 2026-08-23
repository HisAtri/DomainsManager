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

当前已提供 FastAPI 应用骨架、健康检查、本地用户认证、用户域名 CRUD、刷新任务和检查历史、基础管理员查询与封禁接口。使用 `domainsmanager-worker` 独立处理刷新任务，使用 `domainsmanager-seed` 创建前端联调示例数据。真实 OAuth2、通知和后台调度将按
[后端 API 规范](docs/api/README.md) 逐步实现。开发环境启动：

```powershell
uv sync --extra api
$env:DOMAINSMANAGER_DATABASE_TYPE = "postgresql"
$env:DOMAINSMANAGER_DATABASE_HOST = "localhost"
$env:DOMAINSMANAGER_DATABASE_PORT = "5432"
$env:DOMAINSMANAGER_DATABASE_NAME = "domainsmanager"
$env:DOMAINSMANAGER_DATABASE_USER = "domainsmanager"
$env:DOMAINSMANAGER_DATABASE_PASSWORD = "change-me"
$env:DOMAINSMANAGER_DATABASE_SSL_MODE = "disable"
$env:DOMAINSMANAGER_JWT_SECRET_KEY = "replace-me"
$env:DOMAINSMANAGER_REFRESH_TOKEN_PEPPER = "replace-me"
uv run alembic upgrade head
uv run domainsmanager-api
```

服务默认监听 `http://127.0.0.1:7920`，健康检查为 `/health/live` 和
`/health/ready`。服务启动时会自动将配置的数据库升级到最新迁移版本；首次部署可临时设置
`DOMAINSMANAGER_BOOTSTRAP_ADMIN_USERNAME` 和
`DOMAINSMANAGER_BOOTSTRAP_ADMIN_PASSWORD`；只有数据库没有任何用户时才会创建管理员，后续启动
不会用这些变量修改或新增账号。

## 文档

- [架构与本次重构说明](docs/architecture.md)
- [ccTLD WHOIS Profile 扩展指南](docs/whois-profiles.md)
- [数据库缓存接入指南](docs/cache-backends.md)
- [数据库设计](docs/database-design.md)
- [后端 API 规范](docs/api/README.md)
- [后续开发工作计划](docs/development-plan.md)

## 测试

```powershell
uv run pytest -m "not network" -ra
```

`DomainLookup` 是业务层稳定入口，只公开域名标准化和批量查询两个方法。缓存、客户端、
解析器和原始响应属于包内实现；高级缓存适配器通过 `domainsmanager_lookup.spi` 接入。
旧 `modules.*` 导入路径暂时保留为兼容层。
