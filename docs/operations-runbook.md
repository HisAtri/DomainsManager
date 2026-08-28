# 生产运维手册

## 备份与恢复

在维护窗口前停止新发布，并确认 API、Worker、Scheduler 和 Notifier 使用同一数据库 revision。PostgreSQL 逻辑备份示例：

连接池、连接超时和命令超时通过 `DOMAINSMANAGER_DATABASE_POOL_SIZE`、`DOMAINSMANAGER_DATABASE_CONNECT_TIMEOUT` 与 `DOMAINSMANAGER_DATABASE_COMMAND_TIMEOUT` 配置；容量调整前应在专用 PostgreSQL 环境运行 `pytest -m postgres -ra`，其中包含管理、队列和安全审计索引查询计划验证。

## PostgreSQL 容量与故障演练

单个进程最多占用 `pool_size + max_overflow` 个连接。部署前按 API、Worker、Scheduler 和 Notifier 的实例数分别计算上限，总和必须低于 PostgreSQL `max_connections`，并为 migration、监控和人工处置保留余量。`pool_timeout` 控制连接池耗尽时的等待上限，`command_timeout` 控制单条 SQL 的执行上限。

专用 PostgreSQL 验收包含连接池耗尽、等待超时、释放后的连接恢复，以及 SQL 超时后的新连接健康检查。故障演练不得使用生产库；运行 `pytest -m postgres -ra` 后还应确认 `/health/ready`、`domainsmanager-monitor` 和后台组件结构化日志恢复正常。

```powershell
pg_dump --format=custom --file domainsmanager-before-release.dump $env:DATABASE_URL
```

恢复只能在隔离、可清空的目标数据库进行：先创建空数据库，执行 `pg_restore --clean --if-exists`，再运行 `domainsmanager-migrate` 并访问 `/health/ready`。恢复演练至少核对 Alembic revision、管理员登录、域名读取和后台组件能够连接数据库；不得把生产备份恢复到共享测试库。

## 滚动升级

1. 记录当前应用版本与数据库 revision，并完成备份；
2. 在单独 migration Job 中运行 `domainsmanager-migrate`；
3. 先滚动部署 API，等待每个实例 `/health/ready` 返回成功；
4. 再滚动部署 Worker、Scheduler 和 Notifier；每种组件同一时刻至少保留一个健康实例；
5. 观察结构化访问日志、任务租约、队列积压和通知失败数；异常时停止继续扩容。

API 默认不会自动迁移数据库。不要通过 `DOMAINSMANAGER_MIGRATE_ON_STARTUP` 在生产绕过 migration Job。

## Migration 回滚

回滚应用前先确认目标旧版本能读取当前 schema。若 migration 向后不兼容，优先从备份恢复，而不是直接降级。只有在隔离环境验证过的 revision 才可执行：

```powershell
domainsmanager-migrate <目标 revision>
```

回滚后重新运行 `/health/ready`，并验证 API、Worker、Scheduler、Notifier 与目标版本一致。对已删除的业务数据，清理命令没有撤销能力，只能通过已验证的备份恢复。
