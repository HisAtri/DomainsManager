# 可观测性基线

## HTTP 访问日志

API 为每个 HTTP 请求写入一条紧凑 JSON 日志，logger 名称为 `domainsmanager.access`。字段固定为：

- `event`：当前为 `http_request`；
- `request_id`：客户端提供且通过校验的请求 ID，或服务端生成的 ID；
- `method`、`path` 和 `status_code`；
- `duration_ms`：服务端处理耗时，单位为毫秒。

访问日志不记录查询字符串、请求头、Cookie、请求体或响应体。部署侧不得重新开启 Uvicorn 的详细访问日志后把这些敏感字段拼入同一事件；如需关联用户操作，应使用 `request_id` 连接安全审计记录。

当前基线已提供应用日志、后台队列快照和结构化告警事件，但不绑定日志采集平台，也未引入 Tracing 或统一告警阈值管理。

## 管理员运行快照

`GET /api/v1/admin/operations/metrics` 仅对管理员开放，返回刷新任务、通知 Outbox、过期租约和到期未执行监控的聚合数量。响应不包含用户、域名、凭据或错误原文；它作为告警规则和管理端状态面板的共同数据源。

前端“运行状态”页面按需读取该快照并提供手动刷新，不启用浏览器端轮询；应由部署侧的监控采集或告警进程负责持续检查。

## 运维监控命令

`domainsmanager-monitor` 读取同一快照并向标准错误输出紧凑 JSON：每次均有一条 `operational_metrics`，死信、过期租约或逾期监控数量非零时另有对应的 `operational_alert`。事件仅包含聚合数量和生成时间，不含域名、用户、凭据或错误原文。该命令不执行迁移，适合由 cron、systemd timer 或外部采集器定期调用。

## 安全审计查询

管理员可通过 `GET /api/v1/admin/security-audit-events` 按事件类型、操作者和发生时间筛选审计记录。响应仅提供事件标识、事件类型、操作者/目标标识及发生时间；审计元数据、IP 哈希和请求 ID 不通过该接口返回。

`domainsmanager-audit-monitor --since <带时区时间>` 聚合该时间点以来的 Refresh Token 重放事件。每次输出 `security_audit_metrics`；数量非零时另输出 `security_alert`。部署侧应在每次成功采集后推进 `--since`，命令不执行迁移。
