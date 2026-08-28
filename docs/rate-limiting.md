# 认证接口限流

API 对登录、注册、Token 刷新和预留的密码重置 POST 路径启用固定窗口限流。默认每个直接连接端地址、每个端点在 60 秒内允许 20 次请求，可通过以下环境变量调整：

- `DOMAINSMANAGER_AUTH_RATE_LIMIT_ATTEMPTS`；
- `DOMAINSMANAGER_AUTH_RATE_LIMIT_WINDOW_SECONDS`。

超过限制时返回 `429 rate_limited`、`Retry-After`、`Cache-Control: no-store` 和当前 `request_id`。不同认证端点分别计数，成功与失败请求都会占用额度，避免通过响应差异绕过计数。

当前计数器是单进程内存状态，只读取 ASGI 直接连接端地址，不信任可伪造的 `X-Forwarded-For`。多进程或多实例生产部署必须在可信入口增加共享限流；应用内限流作为单实例和入口配置失误时的基础保护。
