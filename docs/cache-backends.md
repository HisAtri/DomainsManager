# 数据库缓存接入指南

## 1. 当前状态

查询服务不直接依赖数据库。缓存通过两个抽象接口提供：

- `DomainResponseCache`：缓存原始 RDAP/WHOIS 报文；
- `RegistryEndpointCache`：缓存 IANA 发现的 WHOIS/RDAP 端点。

默认实现是进程内内存缓存，仅适合开发和测试。

## 2. 原始报文缓存接口

```python
class DomainResponseCache(ABC):
    async def get_fresh(
        self,
        domain: str,
        protocol: LookupProtocol,
        now: datetime,
    ) -> RawLookupResponse | None:
        ...

    async def save(self, response: RawLookupResponse) -> None:
        ...
```

推荐唯一键：

```text
(domain, protocol)
```

如果需要保留历史报文，可以使用独立历史表，并通过查询选取最新且未过期的记录。

建议字段：

```text
id
domain
protocol
endpoint
body
status_code
content_type
fetched_at
expires_at
content_hash
created_at
```

`get_fresh()` 必须同时满足：

- 域名精确匹配标准化后的 `registrable_domain`；
- 协议匹配；
- `expires_at > now`；
- 如果有多条，返回最新记录。

## 3. 注册局端点缓存接口

```python
class RegistryEndpointCache(ABC):
    async def get_fresh(
        self,
        key: str,
        now: datetime,
    ) -> RegistryEndpoint | None:
        ...

    async def save(self, endpoint: RegistryEndpoint) -> None:
        ...
```

推荐唯一键：

```text
key = public_suffix
```

建议字段：

```text
id
lookup_key
tld
whois_server
rdap_urls_json
source
fetched_at
expires_at
last_error
created_at
updated_at
```

需要注意：缓存键可以是 `co.uk`，但 IANA Root Database 查询使用的是 `uk`。

## 4. 示例数据库适配器

下面是伪代码，具体 ORM 可以是 SQLAlchemy、SQLModel 或其他异步数据库层：

```python
class DatabaseDomainResponseCache(DomainResponseCache):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def get_fresh(self, domain, protocol, now):
        async with self._session_factory() as session:
            row = await find_latest_response(
                session,
                domain=domain,
                protocol=protocol,
                expires_after=now,
            )
            return None if row is None else RawLookupResponse.model_validate(row)

    async def save(self, response):
        async with self._session_factory() as session:
            await upsert_response(session, response.model_dump())
            await session.commit()
```

注入服务：

```python
service = DomainLookupService(
    response_cache=DatabaseDomainResponseCache(session_factory),
    endpoint_cache=DatabaseRegistryEndpointCache(session_factory),
)
```

## 5. 时间要求

所有缓存时间应使用带时区的 UTC `datetime`：

```python
datetime.now(timezone.utc)
```

数据库字段也应保存 UTC。不要把域名自身的过期时间当作查询缓存 TTL：

- 域名过期时间是注册局业务数据；
- 缓存过期时间表示多久后应重新请求注册局；
- 两者语义完全不同。

## 6. 并发与幂等

生产实现应处理多个 Worker 同时刷新同一键：

- 为唯一键建立数据库唯一约束；
- 使用 UPSERT；
- 事务内重新检查新鲜记录；
- 必要时使用行锁或分布式锁；
- 不要仅依赖进程内 `asyncio.Lock`。

保存接口必须是幂等的。相同响应重复保存不能破坏当前有效缓存。

## 7. 原始响应和解析快照

建议保留原始报文，并可另外建立解析快照表：

```text
domain_snapshot
----------------
id
domain
raw_response_id
parser_key
parser_version
parsed_data_json
parsed_at
expires_at
```

这样 WHOIS Profile 升级后可以：

1. 找到旧版本解析快照；
2. 读取对应原始报文；
3. 用新 Parser 重新解析；
4. 保存新快照；
5. 不重新请求注册局。

## 8. 负缓存

WHOIS 未注册、RDAP 404、限流和临时故障不能使用同一 TTL：

- `NOT_FOUND`：可以使用较短负缓存，例如 30 分钟到数小时；
- `RATE_LIMITED`：遵循 Retry-After 或注册局限制；
- `TEMPORARY_FAILURE`：使用很短退避时间；
- `ACCESS_DENIED`：通常需要人工处理配置，而不是高频重试。

当前应用服务尚未实现完整负缓存模型。数据库实现前建议先增加协议无关的
`LookupOutcome`，避免把非成功响应当作解析异常反复请求。

## 9. 安全与数据治理

WHOIS/RDAP 原始报文可能包含联系信息。数据库实现需要考虑：

- 访问控制；
- 加密存储或磁盘加密；
- 日志脱敏；
- 数据保留期限；
- 删除和审计策略；
- 不把完整原始响应输出到普通应用日志。
