# 业务记录清理

M6 不为任何记录类型隐式决定保留天数。运维人员必须为每次清理提供带时区的明确截止时间，并先运行 dry-run：

```powershell
domainsmanager-cleanup --before 2026-01-01T00:00:00Z sessions refresh_tokens idempotency tasks notifications audit leases
```

确认输出数量后，增加 `--apply` 才会执行删除。可选目标及其安全边界：

- `sessions`：已超过绝对过期时间的会话；关联 Refresh Token 由外键级联删除；
- `refresh_tokens`、`idempotency`、`leases`：各自超过有效期的运行记录；
- `tasks`：仅已完成的终态刷新任务，按完成时间清理；
- `notifications`：仅 `sent` 或 `dead_letter` 的投递记录，按最后更新时间清理；
- `audit`：安全审计记录，按发生时间清理。

清理工具不会删除域名、检查历史、通知规则、活跃任务、运行中的投递或缓存/原始查询记录。删除前应完成备份并记录实际 cutoff、目标和 dry-run 数量；保留周期由运营与合规要求另行决定。
