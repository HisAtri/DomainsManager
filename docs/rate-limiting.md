# 分级速率限制

应用层仅对已认证用户限流，不根据 IP 或转发地址头计数。登录、注册、Token 刷新和其他匿名入口不受应用层速率限制，应由可信反向代理或 CDN 的 Anti-Bot/IP 策略保护。

默认采用固定窗口，管理员可在“系统设置 → 速率限制”中即时调整：

- 普通接口：每个用户 120 秒 300 次；
- 高成本接口：每个用户 120 秒 30 次。

新增域名、用户手动刷新域名和管理员手动刷新域名属于高成本接口；其他使用认证用户身份的 API 属于普通接口。后台 Worker、Scheduler 和 Notifier 不走 HTTP 用户限流。

超限请求返回 `429 rate_limited`，并包含 `Retry-After`、`Cache-Control: no-store` 和 request ID。

## 后端

`DOMAINSMANAGER_RATE_LIMIT_BACKEND=memory` 是默认值，不需要额外服务，适合单进程单实例部署。

多实例部署请安装 `domainsmanager[rate-limit-redis]`，并配置：

```env
DOMAINSMANAGER_RATE_LIMIT_BACKEND=redis
DOMAINSMANAGER_RATE_LIMIT_REDIS_URL=redis://redis:6379/0
DOMAINSMANAGER_RATE_LIMIT_REDIS_KEY_PREFIX=domainsmanager:rate-limit
```

Redis 模式会在服务启动时执行 `PING`。Redis 地址缺失、客户端依赖未安装或 Redis 不可用时，服务会报错退出，且不会回退到内存实现。
