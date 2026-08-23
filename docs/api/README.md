# 后端 API 规范

## 刷新任务执行策略

Worker 在外部查询期间以任务租约三分之一的间隔续约。成功检查会记录稳定快照哈希和变更字段，并将下次常规检查时间设置为 `DOMAINSMANAGER_CHECK_INTERVAL_SECONDS`（默认 86400 秒）之后；失败检查不会覆盖最近一次成功快照。`changed_fields` 仅比较注册商、状态、注册/到期/注册局更新时间、名称服务器和 DNSSEC 状态。该内部调度时间当前不通过普通用户域名 API 暴露。

刷新任务默认最多尝试 5 次。`rate_limited` 与 `temporary_failure` 使用指数退避重新排队；其他错误或达到重试上限后进入 `failed`。可通过 `DOMAINSMANAGER_TASK_MAX_ATTEMPTS`、`DOMAINSMANAGER_TASK_RETRY_BASE_SECONDS`、`DOMAINSMANAGER_TASK_RETRY_MAX_SECONDS` 和 `DOMAINSMANAGER_TASK_LEASE_SECONDS` 调整策略。Worker 租约保持由后续 Worker 心跳实现。

[openapi.yaml](openapi.yaml) 是 FastAPI 后端的初版契约，采用 OpenAPI 3.1，统一前缀为
`/api/v1`。当前已实现应用骨架、根级健康检查、本地认证、当前用户、用户域名 CRUD、刷新任务、检查历史和基础管理员接口。OAuth2 仅提供 Provider 空配置状态；其余业务路由按本文档约定逐步交付。

## 1. 资源和权限边界

API 分为以下资源组：

| 分组 | 路径 | 权限 |
| --- | --- | --- |
| 注册、登录、Token 轮换 | `/auth/*` | 按端点公开或 Bearer Token |
| 当前用户资料和设置 | `/auth/me/*` | 当前用户 |
| 第三方登录和绑定 | `/auth/oauth2/*` | 按端点公开或当前用户 |
| 用户域名、检查和任务 | `/domains/*`、`/tasks/*` | 仅资源所有者 |
| 管理员用户管理 | `/admin/users/*` | `admin` |
| 管理员全局域名管理 | `/admin/domains/*`、`/admin/domain-checks` | `admin` |

访问非本人资源时，普通用户接口统一返回 `404`，不使用 `403` 暴露资源是否存在。管理员接口
仍需先验证 `admin` 角色。角色必须存放在独立、受保护的数据字段中，不能放进用户可编辑的
`preferences`。

## 2. 认证生命周期

本系统计划签发自己的短时 Access Token 和长时 Refresh Token：

1. Access Token 通过 `Authorization: Bearer <token>` 使用，仅包含最少身份和授权声明；
2. Refresh Token 仅在登录、轮换和退出端点传输，服务端只保存其密码学哈希；
3. 每次刷新都会轮换 Refresh Token，旧 Token 立即失效；发现旧 Token 重放时撤销该 Token
   家族；
4. 退出撤销当前会话；修改密码撤销其他会话；封禁撤销目标用户全部会话；
5. 登录失败不区分用户名不存在、密码错误和账号被禁用，避免账号枚举；
6. 密码使用 Argon2id 保存，日志不得记录密码、Token、TOTP Secret 或 OAuth 授权码。

密码策略只要求长度为 6-256 字符，不检查大小写、数字、符号或常见密码。JWT Secret 和
Refresh Token Pepper 由部署环境提供，应用只要求非空，不检查长度或熵值；生产部署负责使用
符合自身安全要求的 Secret。

首次管理员可通过成对的 `DOMAINSMANAGER_BOOTSTRAP_ADMIN_USERNAME` 和
`DOMAINSMANAGER_BOOTSTRAP_ADMIN_PASSWORD` 引导创建。只有 `app_user` 表没有任何用户时才会
读取并应用这两个值；数据库一旦存在任意用户，后续启动不得用环境变量创建、修改或提权账号。
除这一首次引导外，用户和管理员操作优先通过 HTTP API 完成。

OAuth2 仅预留 GitHub、Google 等第三方登录和账号绑定，不把 DomainsManager 定义为 OAuth2
Authorization Server。`state` 必须高熵、一次性、短时有效并与回调地址绑定；支持 OIDC 的
Provider 还应验证 `nonce`、issuer 和 audience。解绑前必须确认用户仍有其他可用登录方式。

## 3. 用户和管理员操作

`AppUser` 当前已有用户名、密码哈希、邮箱、用户设置、激活状态和登录时间。API 把账号状态规范为
`active` 和 `banned`，把权限规范为 `user` 和 `admin`。

管理员接口不会返回 `password_hash`、TOTP Secret、Access/Refresh Token 或 OAuth Provider
Token。封禁要求原因并立即撤销会话；管理员不能封禁自己。管理员密码重置只创建一次性重置
流程，响应不得包含明文密码或重置 Token。管理员写操作和密码、OAuth 绑定等安全操作均写入
`SecurityAuditEvent`，至少记录操作者、目标、事件类型、请求 ID、时间和脱敏元数据。

用户设置采用白名单 Schema，目前预留：

- `locale`；
- `timezone`；
- `default_monitor_enabled`；
- `expiration_warning_days`。

客户端不能提交任意 JSON 覆盖整个 `preferences`。

## 4. 域名资源语义

新增域名先调用 `DomainLookup.normalize()`，将 Unicode 和 Punycode 等价输入归一为相同的
`name_ascii`。M1 仅接受可注册域名，例如 `example.com`；子域名会被拒绝。数据库通过
`(user_id, name_ascii)` 唯一约束处理并发重复创建。创建同名软删除记录会恢复该记录并返回
`200`，首次创建返回 `201`。

`ManagedDomain` 响应由三类数据组成：

- `identity`：映射 `DomainIdentity` 和 `ManagedDomain` 的标准化名称字段；
- `snapshot`：映射 `DomainSnapshot` 和 `ManagedDomain` 的最新注册局快照；
- 本地属性：监控开关、续期模式、备注、调度时间及 `version`。

普通更新只允许修改本地属性，客户端不能直接写注册商、到期时间、状态、名称服务器或 DNSSEC
等注册局数据。更新使用 `ETag: "<version>"` 和 `If-Match` 实施乐观并发；缺少前置条件返回
`428`，版本过期返回 `409`。

删除采用软删除：资源默认从用户列表和调度器中隐藏，检查历史与审计按保留策略继续保存。M1 的
DELETE 天然幂等，不要求 `Idempotency-Key`；需要持久化请求幂等的刷新和管理员写操作留待 M2。

## 5. 刷新和检查任务

WHOIS/RDAP 请求延迟和上游限流不适合占用普通 HTTP 请求，所以刷新统一为异步任务：

1. `POST /domains/{domain_id}/refresh` 返回 `202 Accepted` 和 `RefreshTask`；
2. `Location` 指向 `/tasks/{task_id}`，客户端轮询任务状态；
3. 状态为 `queued`、`running`、`succeeded`、`failed` 或 `cancelled`；
4. 成功任务关联 `DomainCheck`，失败检查同样写入历史；
5. 刷新必须提交 `Idempotency-Key`，相同用户、资源、操作及请求参数复用原任务；
6. `force_refresh` 跳过域名响应缓存，管理员还可选择 `refresh_endpoint` 跳过端点缓存；
7. 外部查询在数据库事务外执行，完成后以短事务写入检查和最新快照。

成功检查更新 `ManagedDomain` 最新快照；失败检查只更新检查时间和结果，不覆盖上一次成功快照。
检查接口返回解析结果和脱敏错误，不直接暴露原始 WHOIS/RDAP 报文。

## 6. 通用 HTTP 约定

- 所有时间是带时区的 UTC RFC 3339 `date-time`；
- 标识符使用 UUID；
- 分页响应统一为 `items`、`page`、`page_size` 和 `total`；
- `page_size` 最大为 100；
- 列表排序用 `sort`，前缀 `-` 表示降序；
- 创建、刷新、封禁和删除等可重试写操作使用 `Idempotency-Key`；
- 错误统一为 `code`、`message`、`details`、`request_id`，底层网络和数据库异常不得直接返回；
- `401` 表示 Token 无效或过期，`403` 表示权限不足或账号禁用，`404` 表示不可见资源，
  `409` 表示状态或并发冲突，`422` 表示输入错误，`429` 表示本服务或上游限流；
- API 应为每个请求生成或传播 request ID，并通过响应头同步返回，便于关联审计和日志。

库层当前的查询错误码为 `invalid_domain`、`not_found`、`unsupported`、`rate_limited`、
`temporary_failure` 和 `unexpected_response`。实现 HTTP 层时应保留这些稳定值；当前部分分类仍依赖
错误消息匹配，后续应把内部异常改为结构化错误码。

## 7. 与现有代码映射

| API 行为 | 现有能力 | 后续实现 |
| --- | --- | --- |
| 域名标准化 | `DomainLookup.normalize()` | 调用并映射 `InvalidDomainError` |
| 域名查询 | `DomainLookup.lookup()` | 后台 Worker 和任务编排 |
| 查询缓存 | `SqlAlchemyLookupStore` | 在应用生命周期注入 `DomainLookup` |
| 用户数据 | `AppUser`、认证会话 | User/UoW/认证服务已实现；管理员管理待实现 |
| 管理域名 | `ManagedDomain` | Domain Repository、M1 CRUD、软删除和 M2 快照写入已实现 |
| 检查历史 | `DomainCheck` | 成功/失败检查持久化、快照哈希和变化比较已实现 |
| 安全审计 | `SecurityAuditEvent` | Audit Repository 和统一审计服务 |

业务路由不得导入 `domainsmanager_lookup._internal` 或旧 `modules.*`；只使用包根公开 DTO、
`DomainLookup` 和明确的持久化 Repository。

## 8. 实施前迁移清单

当前数据库模型不足以完整实现契约，需要后续 Alembic 迁移：

- `AppUser` 增加独立角色、封禁时间和原因，必要时拆分 `is_active` 与封禁状态；
- 增加认证会话表，保存 Refresh Token 哈希、Token 家族、失效/撤销时间和客户端元数据；
- 核对现有 `totp_secret_ciphertext`，增加明确的 TOTP 启用状态；
- `ManagedDomain` 增加软删除时间和删除操作者，默认查询和调度排除软删除记录；
- 增加异步任务表或定义等价的外部队列持久化契约；
- 增加 OAuth Provider 配置、外部身份映射及 `state`/`nonce` 短期存储；
- 如需可靠重试，增加幂等键记录表并保存请求指纹和响应/任务引用；
- 增加用户、域名、检查、任务和审计 Repository 的集成测试。

## 9. 当前状态

FastAPI 应用工厂、配置、资源生命周期、请求 ID、统一错误边界、健康检查，本地注册、登录、
退出、Token 轮换、当前用户资料、改密和设置，以及用户域名列表、创建、详情、ETag 更新与软删除
已实现。管理员业务路由、后台 Worker、OAuth Provider 集成和对应后续迁移仍待实现。后续实现应以
[openapi.yaml](openapi.yaml) 为行为基线，并在修改 HTTP 行为时同步更新规范和契约测试。
