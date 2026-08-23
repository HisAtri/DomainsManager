# 实施变更记录

本文档记录已合并的后端能力及其对 API、前端对接和运行配置的影响。功能实现必须在同一提交中更新本记录；HTTP 契约变更还必须同步更新 `docs/api/openapi.yaml`。

## 2026-08-24 — M2 刷新任务验收收尾

### 后端变更

- Worker 在 RDAP/WHOIS 查询期间每隔任务租约的三分之一续期一次；若租约已被其他 Worker 接管，当前 Worker 不再写入任务结果。
- 成功查询会为 JSON 快照生成稳定的 SHA-256 哈希，并与最近一次成功快照比较 `registrar`、`statuses`、`registered_at`、`expires_at`、`updated_at`、`nameservers` 和 `dnssec_enabled`。
- `DomainCheck.changed_fields` 仅包含上述发生变化的字段；首次成功检查保持为空数组。
- 成功查询后会写入 `ManagedDomain.next_check_at`。默认周期为 24 小时，配置项为 `DOMAINSMANAGER_CHECK_INTERVAL_SECONDS`。
- RFC 3339 快照时间在持久化边界转换为 UTC `datetime` 后再写入域名最新状态，避免把 JSON 字符串写入数据库时间列。

### API 与前端对接

- 现有 `GET /domains/{id}/checks` 和 `GET /domains/{id}/checks/{check_id}` 已返回 `changed_fields`，本次没有新增或修改 HTTP 字段，因此前端无需修改请求、轮询或类型定义。
- 前端可在检查历史中将非空 `changed_fields` 渲染为“本次变更字段”；字段为空代表首次成功检查或快照未变化。
- `next_check_at` 目前是后端调度预备数据，普通用户域名 API 暂不暴露；M3 Scheduler API 设计时再决定是否展示。

### 验证

- `tests/test_task_policy.py` 覆盖租约续期、防止第二 Worker 重领、快照差异和下次检查时间。
- 每次变更后运行目标测试、静态检查和全量测试；PostgreSQL 多 Worker 并发测试将在 M2 最终验收中补齐。
