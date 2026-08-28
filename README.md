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
uv run domainsmanager-server
```

服务默认监听 `http://127.0.0.1:7920`，健康检查为 `/health/live` 和
`/health/ready`。生产部署应先运行 `uv run domainsmanager-migrate` 将数据库升级到最新 revision；仅本地兼容场景可设置 `DOMAINSMANAGER_MIGRATE_ON_STARTUP=true` 让 API 启动时迁移。首次部署可临时设置
`DOMAINSMANAGER_BOOTSTRAP_ADMIN_USERNAME` 和
`DOMAINSMANAGER_BOOTSTRAP_ADMIN_PASSWORD`；只有数据库没有任何用户时才会创建管理员，后续启动
不会用这些变量修改或新增账号。

推荐使用 `uv run domainsmanager-server` 启动完整后端：HTTP API、刷新任务 Worker、定时调度器和通知 Worker 会在同一进程中运行。若部署时需要分别扩缩容，仍可单独启动各组件。刷新任务由独立 Worker 执行：`uv run domainsmanager-worker`（也支持 `python -m domainsmanager_api.worker`）；部署前应先执行独立 migration 命令。可使用 `DOMAINSMANAGER_WORKER_ID` 指定稳定标识，并使用 `DOMAINSMANAGER_WORKER_POLL_INTERVAL_SECONDS` 调整空队列轮询间隔。
定时调度由独立 Scheduler 执行：`uv run domainsmanager-scheduler`（也支持 `python -m domainsmanager_api.scheduler`）。它扫描到期的已启用域名并创建刷新任务；可通过 `DOMAINSMANAGER_SCHEDULER_POLL_INTERVAL_SECONDS` 和 `DOMAINSMANAGER_SCHEDULER_BATCH_SIZE` 调整轮询与批量大小。
通知投递由独立进程执行：`uv run domainsmanager-notifier`。Webhook 规则通过 HTTP POST 投递；邮件规则投递到账户邮箱，需配置 SMTP 主机和发件人。投递失败不会影响域名检查，任务会按 Outbox 状态机重试并最终进入死信。

生产发布在 migration 与配置注入完成后，可运行 `uv run domainsmanager-verify-release` 只读验证数据库 revision 并输出当前队列快照；数据库未就绪时命令返回非零退出码。

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
