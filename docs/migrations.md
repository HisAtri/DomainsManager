# 数据库迁移运行方式

API、Worker、Scheduler 和 Notifier 不会在启动时自动执行 migration。部署流程应在发布应用实例前，使用相同数据库环境变量运行：

```powershell
domainsmanager-migrate
```

命令默认升级到 `head`，也可传入 Alembic revision。失败时不得启动新版本应用；成功后再按滚动发布流程启动 API 与后台组件。

`DOMAINSMANAGER_MIGRATE_ON_STARTUP=true` 仅用于本地开发和兼容性测试，生产部署不得设置。升级前完成备份，回滚前先验证目标 revision 与应用版本兼容性。
