# 生产运维手册

## 备份与恢复

在维护窗口前停止新发布，并确认 API、Worker、Scheduler 和 Notifier 使用同一数据库 revision。PostgreSQL 逻辑备份示例：

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
