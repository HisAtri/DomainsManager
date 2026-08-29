# Webhook 投递规范

## 事件格式

Webhook 使用 HTTPS `POST` 投递 JSON。每次入队生成不可变事件，重试沿用相同的 `id`：

```json
{
  "id": "f3491fd7-ef27-48a0-8f44-57e9d9a4680c",
  "type": "domain.status_changed",
  "api_version": "2026-08-30",
  "created_at": "2026-08-30T12:34:56Z",
  "webhook": {
    "id": "c56e3b6c-d0cd-4f17-a21a-2811495a76ce",
    "name": "生产环境告警"
  },
  "data": {
    "domain_id": "…",
    "check_id": "…",
    "changed_fields": ["statuses"]
  }
}
```

支持的事件为 `domain.expiration_warning`、`domain.status_changed` 和
`domain.query_failed`。`webhook.name` 是用户配置名称的入队时快照；规则改名不改变已入队事件。

## 出站安全边界

- Endpoint 只允许 `https`，端口必须省略或为 `443`；URL 不允许凭据和 fragment。
- TLS 证书严格验证，请求方法固定为 `POST`，禁止跟随重定向。
- 当前不限制目标 IP、私网或 DNS 解析结果。
- Webhook Client 不读取环境代理，只使用管理员配置的 `webhook_proxy_url`；支持 `http` 和 `socks5`。
- 响应采用流式接口，只读取状态与必要的 `Retry-After` 头，随后关闭响应；不读取或解压响应体。
- 当前不提供 HMAC 签名，Endpoint URL 应视为 bearer endpoint。

## 成功、失败与重试

- `2xx`：成功。
- `301`/`302`：拒绝重定向，不重试。
- `429`：速率受限并重试，优先采用 `Retry-After`。
- `408` 和 `5xx`：重试；其他 HTTP 状态终止自动重试。
- 网络、代理和连接中断可重试；TLS 验证失败不重试。

投递历史只向用户返回脱敏结果。`2xx`、`301`、`302`、`429` 显示实际状态码，其他状态只显示
`3**`、`4**` 或 `5**`。响应头、响应体、Endpoint、代理地址和底层异常原文均不写入用户投递历史。
