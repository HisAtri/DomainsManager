# 后续开发工作计划

## 1. 目标与文档边界

本文档用于安排 DomainsManager 后端从当前的认证系统、查询核心和持久化骨架，逐步发展为
可用的域名管理服务，并最终达到生产部署要求。它回答以下问题：

1. 后续功能按什么依赖顺序实施；
2. 每个阶段需要交付哪些能力；
3. 阶段完成时应通过哪些测试和验收；
4. 哪些技术决策需要在对应阶段开始前确定。

本文档不重新定义 HTTP Schema、数据库字段或 RDAP/WHOIS 查询算法。以下专题文档是对应领域的
权威来源：

- HTTP 行为和数据结构：[API OpenAPI 契约](api/openapi.yaml)；
- API 语义和安全边界：[后端 API 规范](api/README.md)；
- 查询核心：[架构与重构说明](architecture.md)；
- 数据库结构和事务原则：[数据库设计](database-design.md)；
- 查询缓存：[数据库缓存接入指南](cache-backends.md)；
- WHOIS 扩展：[WHOIS Profile 扩展指南](whois-profiles.md)。

当路线图与专题规范不一致时，以专题规范为准。需要修改对外行为时，应先更新专题规范和契约
测试，再更新本路线图的阶段状态。

计划不填写未经评估的工期或发布日期。阶段状态仅使用：

- `已完成`：实现、测试和文档已通过阶段验收；
- `待开始`：范围基本明确，但尚未进入实现；
- `阻塞`：存在必须先解决的技术或产品决策；
- `暂缓`：不属于近期核心交付范围。

## 2. 当前基线

基线提交为 `139da95`，开发分支为 `feature/fastapi-server`。

### 2.1 已完成能力

- FastAPI 应用工厂、Settings、lifespan、资源容器和 ASGI 启动入口；
- `/health/live`、`/health/ready`、request ID 和统一应用异常边界；
- 本地用户注册、登录、退出、Refresh Token 轮换和重放撤销；
- Bearer Access Token、当前用户资料、改密和白名单设置；
- 首次管理员环境变量引导，并通过唯一系统状态避免重复初始化；
- 用户、认证 Session、Refresh Token、安全审计的 Repository 和 UoW；
- SQLAlchemy、Alembic、SQLite 快速集成测试和可打包迁移资源；
- `DomainLookup.normalize()` 和 `DomainLookup.lookup()` 公开查询入口；
- IDNA/Punycode 标准化、Public Suffix 识别、RDAP 优先、WHOIS 回退和原始响应缓存。

当前 OpenAPI 定义 37 个 operation，已实现其中 11 个：健康检查 2 个，本地认证和当前用户
9 个。

### 2.2 尚未形成的产品链路

- 用户不能通过 HTTP API 添加、查看、编辑或删除管理域名；
- 没有持久化刷新任务、Worker、Scheduler 或检查历史查询链路；
- 没有管理员用户管理和全局域名管理接口；
- 没有实际运行的通知规则、Outbox Worker、邮件或 Webhook 发送器；
- OAuth2、密码重置和 TOTP 只有契约或字段预留；
- PostgreSQL 并发、部署、监控、备份和数据清理尚未达到生产验收标准。

## 3. 实施原则

后续阶段统一遵循以下约束：

1. PostgreSQL 15+ 是生产数据库基线；SQLite 只用于快速测试，不能证明 PostgreSQL 行锁、
   JSONB、时区或并发语义正确；
2. API、Worker 和 Scheduler 只依赖 `domainsmanager_lookup` 公开 API，不导入
   `domainsmanager_lookup._internal` 或旧 `modules.*`；
3. 外部 RDAP/WHOIS 请求不得持有数据库事务或行锁；
4. 多表业务写入通过 UoW 在短事务内原子提交；
5. 普通用户资源查询必须限定 owner，访问其他用户资源统一返回 `404`；
6. HTTP 行为以 `docs/api/openapi.yaml` 为契约基线；
7. 所有时间使用 UTC aware `datetime`，HTTP 使用 RFC 3339；
8. 密码、Token、TOTP Secret、OAuth code、通知凭据和原始敏感报文不得进入普通日志；
9. 管理员写操作和安全操作必须写入 `SecurityAuditEvent`；
10. 阶段成果拆成可独立验证的提交，不在一个提交中混入无关重构；
11. 每次提交前检查 `.gitignore`、全部未跟踪文件、凭据、暂存清单、`git diff --check`
    和测试结果；
12. 阶段提交完成后保持工作树干净，并同步相关规范和状态文档。

## 4. 里程碑总览

| 里程碑 | 状态 | 核心交付 | 前置依赖 |
| --- | --- | --- | --- |
| M0 | 待开始 | 契约、PostgreSQL 和质量门禁收敛 | 当前基线 |
| M1 | 待开始 | 用户域名持久化和 CRUD | M0 |
| M2 | 待开始 | 刷新任务、Worker 和检查历史 | M1 |
| M3 | 待开始 | 定时调度和通知 | M2 |
| M4 | 待开始 | 管理员用户和全局域名管理 | 用户管理依赖 M1；刷新依赖 M2 |
| M5 | 暂缓 | 密码重置、OAuth2 和 TOTP | M3、M4 |
| M6 | 待开始 | 生产化和数据治理 | 可并行推进，发布前完成 |

## 5. M0：契约与工程基线收敛

**状态：** `待开始`

### 5.1 目标

先消除已实现认证接口与静态 OpenAPI 之间的漂移，并建立 PostgreSQL 和自动质量门禁，避免在
新增域名业务后扩大返工面。

### 5.2 交付范围

- 统一 Refresh Token 请求长度约束；
- 明确 PATCH 接收 `application/merge-patch+json` 还是普通 `application/json`，并统一实现和契约；
- 补齐认证接口实际可能返回的 `403`、`409` 和 `422`；
- 统一静态与运行时 Schema 的 required、nullable 和 security 定义；
- 在 OpenAPI 中声明 request ID、缓存控制、`WWW-Authenticate` 等关键响应头；
- 将框架级 `404`、`405` 和请求解析错误转换成统一错误结构；
- 扩充契约测试，比较媒体类型、请求 Schema、响应码、错误体和安全声明；
- 建立 PostgreSQL 集成测试入口，覆盖迁移、Session、Refresh Token 轮换和行锁；
- 增加格式检查、lint、type-check、coverage 门禁和 wheel 隔离安装测试；
- readiness 检查数据库 revision，而不只是执行 `SELECT 1`；
- 明确生产支持矩阵，清理数据库设计文档中遗留的 SQLite/MySQL 目标描述。

### 5.3 完成条件

- 已实现的 11 个 operation 与静态 OpenAPI 契约一致；
- 空数据库和带旧 revision 的数据库都能升级到 head；
- PostgreSQL 下的认证并发和行锁测试通过；
- CI 能阻止契约漂移、migration 漏打包和测试失败；
- readiness 能识别未迁移或 migration 落后的数据库。

### 5.4 建议提交边界

1. 修正认证契约和统一错误响应；
2. 增加 PostgreSQL 集成测试；
3. 增加 lint、type-check、coverage 和打包门禁；
4. 增强 readiness 和部署配置检查。

## 6. M1：用户域名持久化与 CRUD

**状态：** `待开始`

**前置依赖：** M0

### 6.1 目标

完成第一个核心产品闭环：用户登录后可以添加、查询、编辑和软删除自己的域名，但暂不在 HTTP
请求中同步执行 RDAP/WHOIS 刷新。

### 6.2 交付范围

- 为 `ManagedDomain` 增加 `deleted_at`、`deleted_by_user_id` 和必要索引；
- 明确软删除后同名域名重新添加时恢复原记录；
- 定义 Domain Application DTO、Repository Port 和 SQLAlchemy 实现；
- 创建域名前调用 `DomainLookup.normalize()`；
- 实现当前用户域名列表、创建、详情、PATCH 和软删除；
- 支持分页、过滤和排序；
- 使用 `(user_id, name_ascii)` 保证同一用户业务唯一性；
- 统一 Unicode 与 Punycode 输入；
- 通过 `ETag`、`If-Match` 和 `version` 实现乐观并发控制；
- 只允许修改 `monitor_enabled`、`renewal_mode` 和 `notes` 等本地字段；
- 禁止客户端直接写注册商、到期时间、状态、名称服务器和 DNSSEC 快照；
- 普通用户查询始终限定 owner，并默认排除软删除记录。

### 6.3 完成条件

- Unicode 和 Punycode 等价输入定位同一资源；
- 同一用户并发重复创建最多产生一条记录；
- 不同用户可以分别管理同名域名；
- 跨用户访问返回 `404`，不泄漏资源归属；
- 缺少 `If-Match` 返回 `428`，版本过期返回 `409`；
- 软删除记录默认不参与列表和后续调度；
- Repository、应用服务和 HTTP API 均有测试。

### 6.4 建议提交边界

1. 软删除模型和 Alembic migration；
2. Domain Port、Repository 和 UoW；
3. 域名 CRUD 应用服务；
4. FastAPI 路由、Schema 和契约测试。

## 7. M2：刷新任务、Worker 与检查历史

**状态：** `待开始`

**前置依赖：** M1

### 7.1 目标

将耗时且受上游限流影响的 RDAP/WHOIS 查询从 HTTP 请求中分离，建立可恢复、可追踪、可并发扩展
的异步刷新链路。

### 7.2 默认技术方向

默认采用 PostgreSQL 持久化任务表和独立 Worker，通过 `FOR UPDATE SKIP LOCKED` 抢占任务。
若后续改用 Redis、RabbitMQ 或 Celery，应先更新本计划、部署边界和故障恢复模型。

### 7.3 交付范围

- 新增域名刷新任务和 HTTP 幂等记录模型；
- 实现任务 Repository、claim、lease、heartbeat、retry 和 recovery；
- 实现 `POST /domains/{id}/refresh` 和 `GET /tasks/{id}`；
- 相同 `Idempotency-Key` 和请求指纹复用原任务，不同指纹返回冲突；
- Worker 在事务外调用 `DomainLookup.lookup()`；
- 成功和失败都写入 `DomainCheck`；
- 成功时更新最新快照、`last_successful_check_at`、`last_check_at`、`last_outcome`、
  `next_check_at` 和 `version`；
- 失败时不覆盖最后一次成功快照；
- 实现规范化快照哈希和 `changed_fields`；
- 实现用户检查历史列表和详情；
- 不通过普通 API 暴露原始 WHOIS/RDAP 报文；
- 修复 `SqlAlchemyLookupStore` 并发 publish 和首次 lease 获取语义；
- 将查询错误从中英文消息匹配推进为结构化错误码；
- 评估公开查询结果是否需要增加协议、解析器版本和缓存来源元数据。

### 7.4 完成条件

- HTTP 刷新立即返回 `202`，不等待外部网络；
- 一个任务最多由一个 Worker 执行；
- Worker 异常退出后租约到期可恢复；
- 同请求幂等重试不会创建重复任务或检查记录；
- 失败检查可追溯且不覆盖成功数据；
- `rate_limited`、`temporary_failure` 等错误按规则重试；
- PostgreSQL 双 Worker 并发测试通过。

### 7.5 建议提交边界

1. 任务、幂等和检查持久化模型；
2. Task/Check Repository 和应用服务；
3. Worker 状态机和进程入口；
4. 刷新、任务和检查历史 API；
5. LookupStore 并发修复和结构化错误。

## 8. M3：定时调度与通知

**状态：** `待开始`

**前置依赖：** M2

### 8.1 目标

让系统按计划持续检查域名，并可靠发送到期、状态变化和连续查询失败等通知。

### 8.2 交付范围

- 独立 Scheduler 扫描 `next_check_at` 并幂等入队；
- 定义刷新周期、退避、重试上限和错过任务补偿；
- 多 Scheduler 实例使用数据库约束或租约避免重复调度；
- 实现 `NotificationRule` Repository 和规则服务；
- 在检查结果事务中生成 Notification Outbox；
- 实现邮件和 Webhook 发送适配器；
- 实现 Outbox claim、lease、retry、dead-letter 和状态更新；
- 通知凭据使用加密存储或外部 Secret，不保存在普通 JSON 配置中；
- 为通知规则和发送历史补充 API 契约，再实现对应路由；
- 记录发送耗时、失败原因和重试次数，并提供必要指标。

### 8.3 完成条件

- 多 Scheduler 或 Worker 不重复调度和发送；
- 到期、状态变化和连续失败通知可重试且可追踪；
- 外部通知失败不回滚域名检查事务；
- 重复事件由业务去重键阻止；
- 通知渠道 Secret 不进入日志或普通 API 响应。

### 8.4 建议提交边界

1. Scheduler 和调度规则；
2. 通知规则 Repository 与服务；
3. Outbox Worker 和发送适配器；
4. 通知 API 和运维指标。

## 9. M4：管理员用户与全局域名管理

**状态：** `待开始`

**前置依赖：** 用户管理部分依赖 M1；管理员刷新和检查部分依赖 M2。

### 9.1 目标

交付管理员对用户、域名和检查记录的全局管理能力，同时保持权限隔离、会话即时撤销和完整审计。

### 9.2 交付范围

- 实现 `require_admin` 依赖；
- 管理员用户列表、详情、搜索、过滤和资料编辑；
- 封禁、解封和撤销全部 Session；
- 禁止管理员封禁自己；
- 封禁状态、会话撤销和审计在同一事务提交；
- 实现全局域名列表、详情、过滤和统计；
- 实现全局检查历史和失败统计；
- 实现管理员软删除、强制刷新和明确确认头；
- 所有管理员写操作记录 actor、target、request ID 和脱敏元数据；
- 管理员响应不得包含密码哈希、Token、TOTP Secret、OAuth Token 或原始查询报文；
- 首版不提供通用域名所有权转移；需要时另行设计独立端点和冲突语义。

### 9.3 完成条件

- 非管理员访问管理员 API 统一返回 `403`；
- 封禁立即使现有 Access/Refresh Session 失效；
- 自封禁被拒绝且有安全审计；
- 并发封禁、解封和删除行为具有确定结果；
- 管理员写操作均可通过审计事件追踪；
- 全局列表分页、过滤、排序和统计有 PostgreSQL 集成测试。

### 9.4 建议提交边界

1. 管理员授权依赖和用户查询；
2. 封禁、解封和会话撤销；
3. 全局域名和检查查询；
4. 管理员删除、刷新和审计。

## 10. M5：密码重置、OAuth2 与 TOTP

**状态：** `暂缓`

**前置依赖：** M3 的通知基础和 M4 的管理员能力。

### 10.1 目标

在核心域名管理链路稳定后，补充账号恢复、第三方身份和二次验证能力。

### 10.2 交付范围

- 先补齐密码重置申请、一次性 Token 消费和通知契约，再创建持久化模型；
- 重置 Token 只保存摘要，具备过期、撤销和单次消费语义；
- Provider 配置、外部身份和一次性 OAuth authorization state；
- redirect URI 白名单、PKCE 和 OIDC nonce；
- 第三方登录、账号绑定和解绑；
- 禁止解绑最后一种有效登录方式；
- TOTP Secret 加密、启用确认、验证、禁用和恢复流程；
- 密码重置、OAuth 绑定和 TOTP 操作写安全审计并撤销必要 Session。

### 10.3 完成条件

- state 和重置 Token 单次消费且可过期；
- redirect URI 不接受任意外部地址；
- OIDC 验证 issuer、audience 和 nonce；
- 一个外部身份不能绑定多个本地用户；
- 用户始终保留至少一种可用登录方式；
- TOTP Secret 不以明文落库、返回或写入日志。

### 10.4 建议提交边界

1. 密码重置契约、模型和服务；
2. OAuth 存储与 Provider 协议；
3. 每个外部 Provider 单独提交；
4. TOTP 生命周期单独提交。

## 11. M6：生产化与数据治理

**状态：** `待开始`

M6 可以与 M2-M5 的部分任务并行推进，但正式生产发布前必须完成。

### 11.1 交付范围

- 结构化访问日志、应用指标、Tracing 和告警；
- 安全审计查询、保留和异常行为告警；
- 登录、注册、Token 刷新和密码重置限流；
- PostgreSQL 连接池、查询超时、容量评估和故障演练；
- 原始 WHOIS/RDAP 报文的压缩、加密、大小限制、保留和清理；
- Session、Refresh Token、缓存、任务、审计和通知记录清理策略；
- Docker/Compose 或其他明确的部署模板；
- 反向代理、TLS、Trusted Host、CORS 和 API 文档生产开关；
- 独立 migration Job，不在 API lifespan 中自动迁移；
- 备份恢复、滚动升级和 migration 回滚手册；
- PostgreSQL E2E、网络测试和隔离 wheel 安装进入 CI；
- Worker、Scheduler 和 Outbox 的队列深度、租约超时和失败指标。

### 11.2 完成条件

- API、Worker 和 Scheduler 可独立部署和横向扩展；
- 数据库、JWT、Refresh Pepper 和通知凭据来自部署 Secret；
- 备份恢复和 migration 演练通过；
- 关键异常、积压、限流和外部失败可观测；
- 发布门禁覆盖测试、契约、migration、打包和安全检查；
- 数据保留和删除策略有文档、有任务、有验证记录。

### 11.3 建议提交边界

1. 日志、指标和告警；
2. 限流和安全中间件；
3. 数据清理和加密；
4. 容器、CI 和部署文档；
5. 备份恢复和发布演练。

## 12. 跨阶段验收要求

每个里程碑都必须满足以下通用要求：

- 新应用行为有 unit 测试；
- Repository、migration 和事务行为有 integration 测试；
- HTTP、Worker 或 Provider 边界有 contract 测试；
- 并发、行锁、JSONB、时区和 migration 行为由 PostgreSQL 测试证明，SQLite 不作为替代；
- 默认测试不依赖真实外部网络，网络测试使用独立 marker 显式运行；
- 业务错误使用稳定错误码，不向客户端泄漏底层数据库、网络或解析异常；
- Secret、Token、密码、原始敏感报文不会出现在日志、异常或测试快照中；
- 管理员和安全操作产生可追踪审计事件；
- OpenAPI、API README、数据库文档和根 README 与实现同步；
- migration 从空库和上一个发布 revision 均可升级；
- wheel 包含所有运行包、入口和 migration；
- `.gitignore` 排除 `.env`、本地数据库、缓存、日志、覆盖率和构建产物；
- 提交前检查完整未跟踪文件和 staged diff；
- 阶段提交后工作树保持干净。

## 13. 暂缓与未决事项

以下问题不应在无结论时被静默固化到实现中：

| 决策 | 最晚确定阶段 | 当前默认方向 |
| --- | --- | --- |
| PostgreSQL 任务表或外部队列 | M2 开始前 | PostgreSQL 持久化任务表 |
| `DomainLookup` 是否扩展公开查询元数据 | M2 开始前 | 先设计独立执行结果 DTO |
| 改密后的 Token 续签语义 | M0 | 保持当前 Access 失效行为并修正文档 |
| 邮箱是否唯一 | M4/M5 | 当前不唯一 |
| 密码重置邮件提供方 | M5 | 未决定 |
| 域名、检查、原始报文和审计保留期 | M2/M6 | 未决定 |
| 原始查询报文和通知凭据加密方案 | M3/M6 | 应用级加密或外部 Secret |
| 默认检查周期和抖动 | M2/M3 | 未决定 |
| 任务重试上限、租约和幂等记录保留期 | M2 | 未决定 |
| 首批 OAuth Provider | M5 | 未决定 |
| TOTP 是否进入首个正式版本 | M5 | 暂缓 |

## 14. 路线图维护方式

- 一个阶段开始时，把状态改为实际状态，并记录已确认的技术决策；
- 一个阶段完成时，只在所有完成条件通过后标记为 `已完成`；
- 行为或数据契约变化先更新对应专项规范，再更新本路线图；
- 新需求应归入已有里程碑，只有跨越多个阶段或改变架构方向时才新增里程碑；
- 不在路线图中记录临时调试步骤、个人环境路径或敏感配置；
- 每次正式发布后更新“当前基线”提交和已实现 operation 数量。
