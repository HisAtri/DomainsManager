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

## 2026-08-24 — M3 Scheduler 基础链路

### 后端变更

- 新增独立的 `domainsmanager-scheduler` 进程。它扫描已启用、未软删除且 `next_check_at <= now` 的域名。
- Scheduler 使用数据库行锁和 `SKIP LOCKED` 领取域名；领取、推进 `next_check_at`、创建刷新任务及幂等记录处于同一事务中。
- 默认批量大小为 100，轮询间隔为 10 秒；可通过 `DOMAINSMANAGER_SCHEDULER_BATCH_SIZE` 和 `DOMAINSMANAGER_SCHEDULER_POLL_INTERVAL_SECONDS` 调整。
- Scheduler 创建的任务不跳过查询缓存。任务成功后继续由 Worker 写入下一次检查时间；任务重试或失败遵循既有 M2 策略。

### API 与前端对接

- 本提交未改变 HTTP API 或前端数据类型。Scheduler 是部署侧后台进程，前端继续通过现有任务轮询和检查历史接口观察结果。
- 后续通知规则和投递历史 API 将在 M3 Outbox 提交中记录在本文件和 `frontend/README.md`。

### 验证

- `tests/test_scheduler.py` 覆盖到期域名入队、监控关闭域名排除和重复扫描不重复建任务。

## 2026-08-24 — M3 通知规则基础 API

- 新增 `/api/v1/notification-rules` 的创建和列表接口，规则可绑定单一域名或作为用户全局规则。
- 支持 `expiration`、`status_change` 和 `query_failure` 事件，以及 `email`、`webhook` 渠道。到期规则必须指定提前天数。
- Webhook 规则当前只保存公开 URL；认证凭据、密钥和 Authorization 头不允许通过该接口或 JSON 配置提交。Outbox 与发送 Worker 将在下一提交实现。
- 前端尚未新增页面；可在后续通知设置页面调用上述接口。

## 2026-08-24 — M3 检查结果 Outbox

- 成功检查的状态变化、到期窗口，以及失败检查会在同一数据库事务中创建 `notification_outbox` 记录。
- Outbox 去重键由规则、检查或到期日组成；并发重复写入受唯一约束保护，不会回滚域名检查事务。
- 本次未改变浏览器 API；后续投递历史接口完成后再提供前端页面对接。

## 2026-08-24 — M3 Outbox 投递 Worker

- 新增 `domainsmanager-notifier` 独立进程，使用租约令牌领取 Outbox；投递成功标为 `sent`，失败重试，达到上限标为 `dead_letter`。
- Webhook 使用 HTTP POST；邮件使用用户账户邮箱和 SMTP 配置。SMTP 密码仅从环境变量读取，不写入规则、Outbox、日志或 API 响应。
- 前端可继续通过后续通知历史接口观察投递状态；本提交未新增浏览器端请求。
