# 架构与本次重构说明

## 1. 重构目标

项目需要管理多个域名，并周期性获取它们的注册状态、注册商、注册时间、过期时间、
名称服务器和 DNSSEC 等信息。查询过程中同时涉及：

1. 域名标准化；
2. Public Suffix 识别；
3. 原始域名报文缓存；
4. 注册局 WHOIS/RDAP 端点缓存；
5. IANA 端点发现；
6. RDAP/WHOIS 网络请求；
7. 不同协议和注册局报文的解析；
8. RDAP 与 WHOIS 之间的选择和回退。

如果这些行为都放进 Pydantic 模型的初始化过程，就会造成模型创建隐式访问数据库或
网络、循环依赖、难以测试，以及无法单独替换缓存实现等问题。

本次重构采用以下原则：

> Model 描述数据，Normalizer 负责标准化，Cache 保存历史结果，Client 负责网络，
> Parser 解释报文，Service 编排整个业务流程。

`qwhois.py` 是待弃用代码，本次设计没有引用或修改它。

## 2. 目录结构

```text
modules/
├── models/                 # 纯 Pydantic 数据模型
│   ├── domain.py
│   ├── registry.py
│   └── response.py
├── normalization/          # IDNA 与 Public Suffix 标准化
│   └── domain.py
├── cache/                  # 缓存抽象与临时内存实现
│   ├── base.py
│   └── memory.py
├── clients/                # IANA、RDAP、WHOIS 网络客户端
│   ├── base.py
│   ├── iana.py
│   ├── rdap.py
│   └── whois.py
├── parsers/                # RDAP 和 WHOIS 响应解析入口
│   ├── base.py
│   ├── rdap.py
│   └── whois.py
├── whois_profiles/         # 可插拔 ccTLD WHOIS 处理框架
│   ├── base.py
│   ├── registry.py
│   ├── query.py
│   ├── key_value.py
│   ├── models.py
│   ├── defaults.py
│   └── builtin/
├── services/
│   └── domain_lookup.py    # 查询编排服务
├── errors.py               # 统一异常类型
├── domain.py               # 旧导入路径兼容入口
├── rdap.py                 # 兼容入口
├── suffix.py               # 兼容入口
└── whois.py                # 兼容入口
```

## 3. 完整查询流程

`DomainLookupService.lookup()` 是应用层主要入口。其顺序是：

```text
输入域名
  │
  ├─ 1. IDNA/Punycode 标准化
  ├─ 2. 提取 Public Suffix 和可注册域名
  │
  ├─ 3. 按协议优先级检查所有有效报文缓存
  │      ├─ RDAP 缓存有效 → 解析并返回
  │      └─ WHOIS 缓存有效 → 解析并返回
  │
  ├─ 4. 检查注册局端点缓存
  │      ├─ 有效 → 复用
  │      └─ 无效/不存在 → 通过 IANA 发现并保存
  │
  ├─ 5. 请求 RDAP
  │      ├─ 成功 → 保存原始报文 → 解析 → 返回
  │      └─ 失败/不支持 → 进入下一个协议
  │
  └─ 6. 请求 WHOIS
         ├─ 根据 Public Suffix 找到 WHOIS Profile
         ├─ 构造注册局专用查询
         ├─ 保存原始报文
         └─ 使用 Profile 专用解析器解析
```

一个重要细节是：系统会先检查**所有协议缓存**，然后才进行网络请求。即使协议顺序是
RDAP 优先，只要存在有效 WHOIS 缓存，也不会因为缺少 RDAP 缓存而立即联网。

### 3.1 强制刷新参数

```python
await service.lookup(
    "example.cn",
    force_refresh=True,
    refresh_endpoint=False,
)
```

- `force_refresh=True`：跳过域名原始报文缓存；
- `refresh_endpoint=True`：跳过注册局端点缓存；
- 两者相互独立，刷新域名报文不等于必须刷新稳定的注册局端点。

### 3.2 批量查询

```python
results = await service.lookup_many(names, concurrency=10)
```

批量查询具有以下行为：

- 结果顺序与输入顺序一致；
- `concurrency` 限制同时运行的任务数量；
- 同一 Public Suffix 的并发端点发现会使用锁合并；
- 缓存刚建立时，不会对同一后缀同时发起多次 IANA 请求。

当前锁是单进程、单 Service 实例范围。未来多进程部署时，数据库层仍应通过唯一约束、
事务或分布式锁处理跨进程缓存击穿。

## 4. 数据模型

### 4.1 `NormalizedDomain`

这是只读的标准化域名值对象：

| 字段 | 含义 |
| --- | --- |
| `input_name` | 用户原始输入 |
| `ascii_name` | 完整 Punycode/ASCII 域名 |
| `unicode_name` | Unicode 表示 |
| `subdomain` | 子域名部分 |
| `domain_label` | 可注册域名的主体标签 |
| `public_suffix` | PSL 公共后缀，例如 `co.uk` |
| `registrable_domain` | 可注册域名，例如 `example.co.uk` |
| `tld` | IANA Root Zone 顶级域，例如 `uk` |

模型使用 `frozen=True`，创建后不能修改。它只描述一次标准化结果，不访问数据库和网络。

### 4.2 `DomainInfo`

RDAP 与 WHOIS 最终都转换为统一的 `DomainInfo`：

- 注册局 Handle；
- 注册商名称、IANA ID、URL 和 Abuse 联系方式；
- 域名状态；
- 注册、过期和更新时间；
- 名称服务器；
- DNSSEC；
- 数据来源、来源 URL、抓取时间和解析器版本。

协议解析器只负责将原始响应转换成该模型，不能负责查询或缓存。

### 4.3 `RegistryEndpoint`

记录某个域名空间的注册局服务：

- 缓存键 `key`；
- IANA TLD；
- WHOIS 服务器；
- 一个或多个 RDAP 基础 URL；
- 数据来源；
- `fetched_at` 与 `expires_at`。

### 4.4 `RawLookupResponse`

保存可持久化的原始响应：

- 可注册域名；
- `rdap` 或 `whois` 协议；
- 实际端点；
- 原始文本；
- HTTP 状态码和 Content-Type；
- 抓取与过期时间。

原始报文和解析结果分离后，解析器升级时可以重新解析旧报文，而无需再次请求注册局。

### 4.5 `LookupResult`

查询服务返回：

- 标准化域名；
- 统一的 `DomainInfo`；
- 本次使用的原始报文；
- 是否命中报文缓存；
- 是否命中端点缓存。

## 5. 域名标准化

`DomainNormalizer` 的处理顺序：

1. 去除首尾空白和结尾根点；
2. 转为小写；
3. 使用 IDNA UTS #46 转换为 ASCII；
4. 使用 `tldextract` 提取后缀；
5. 生成不可变 `NormalizedDomain`。

`tldextract.TLDExtract` 配置为 `suffix_list_urls=()`，使用包内 PSL 快照，避免每次创建
对象时隐式访问 Public Suffix List 网络地址。PSL 更新应该作为独立维护任务处理。

私有 PSL 规则当前关闭：

```python
include_psl_private_domains=False
```

这是因为项目管理的是注册局层面的已注册域名，而不是 `blogspot.com` 等平台内部空间。

## 6. IANA 端点发现

IANA Root Zone 与 Public Suffix List 不是同一概念：

- `co.uk` 是 Public Suffix；
- `uk` 才是 IANA Root Zone TLD；
- `github.io` 等私有 PSL 规则不对应独立的 IANA 注册局页面。

因此 `IanaClient` 分别处理：

1. WHOIS：访问 `https://www.iana.org/domains/root/db/{tld}.html`；
2. RDAP：读取 `https://data.iana.org/rdap/dns.json` Bootstrap；
3. 端点缓存键仍使用域名的 `public_suffix`，避免混淆业务域名空间。

RDAP Bootstrap 使用最长后缀匹配，以找到最具体的服务配置。

默认端点缓存 TTL 为 7 天。后续可根据 IANA 数据更新频率调整。

## 7. RDAP

`RdapClient`：

- 使用 IANA Bootstrap 提供的基础 URL；
- 请求 `{base}/domain/{registrable_domain}`；
- 接受 RDAP JSON；
- 跟随 HTTP 重定向；
- 默认超时 20 秒；
- 默认原始报文 TTL 为 6 小时。

`RdapParser` 当前解析：

- `ldhName`/`unicodeName` 和 Handle，并把域名统一为小写 ASCII；
- Status，去除空值和大小写重复项；
- Registration、Expiration、Last Changed 事件；重复的 Registration 取最早时间，其他
  重复事件取最新时间；
- Registrar Entity、IANA ID、vCard 和 Entity Link；
- Registrar 子实体或同级实体中的 Abuse 联系方式；
- `ldhName`/`unicodeName` Nameserver，执行 IDNA 标准化、去重和排序；
- `secureDNS.delegationSigned`。

解析器会先验证 JSON 根节点、HTTP/RDAP 错误对象、`objectClassName`、响应域名以及 RDAP
标准字段的容器类型。错误响应、非 Domain 对象、域名不一致或结构损坏都会统一转换为
`ResponseParseError`，使查询服务可以安全地回退到 WHOIS，而不会泄漏 `AttributeError`、
`TypeError` 等实现异常。缺失或为 `null` 的可选字段按空值处理；单个损坏的 Event、Entity、
Nameserver 或 vCard 条目会被忽略，避免非关键扩展数据影响主体结果。

解析器版本为 `2`。测试覆盖标准响应、Registrar/Abuse 嵌套实体、大小写与 IDNA 标准化、
空可选字段、RDAP 错误对象、非对象 JSON、对象类型/域名不一致、标准字段类型错误，以及
解析失败后的 WHOIS 回退。

RDAP 是 gTLD 的主要数据源。WHOIS Profile 主要用于缺少稳定 RDAP 支持的 ccTLD。

## 8. WHOIS

WHOIS 被拆为三个独立阶段：

1. `WhoisClient` 负责 TCP 连接和响应大小限制；
2. `WhoisQueryStrategy` 负责注册局特定的查询内容与字符编码；
3. `WhoisResponseParser` 负责分类和解析响应。

客户端默认：

- TCP 端口 43；
- 连接和读取超时 15 秒；
- 最大响应 2 MiB；
- 默认报文 TTL 6 小时；
- 阻塞 Socket 查询通过 `asyncio.to_thread()` 执行，不阻塞事件循环。

详细扩展方式见 [WHOIS Profile 扩展指南](whois-profiles.md)。

## 9. 缓存边界

本次只定义缓存契约，不依赖任何数据库 ORM：

```python
class DomainResponseCache(ABC):
    async def get_fresh(domain, protocol, now): ...
    async def save(response): ...


class RegistryEndpointCache(ABC):
    async def get_fresh(key, now): ...
    async def save(endpoint): ...
```

开发和测试期间使用：

- `MemoryDomainResponseCache`；
- `MemoryRegistryEndpointCache`。

它们通过 `asyncio.Lock` 保护进程内字典。生产数据库实现见
[数据库缓存接入指南](cache-backends.md)。

## 10. 异常体系

| 异常 | 含义 |
| --- | --- |
| `DomainNormalizationError` | IDNA 或 Public Suffix 标准化失败 |
| `EndpointDiscoveryError` | IANA 未能发现任何端点 |
| `ProtocolUnavailableError` | 注册局没有指定协议端点 |
| `ResponseParseError` | RDAP/WHOIS 原始响应无法解析 |
| `UnsupportedWhoisProfileError` | ccTLD 没有注册 WHOIS Profile |
| `WhoisResponseError` | WHOIS 返回未注册、限流或其他非成功状态 |
| `LookupFailedError` | 所有允许的查询方式均失败 |

业务代码通常只需要捕获 `LookupFailedError`，底层异常信息会被汇总到错误消息中。

## 11. 兼容性与迁移

为了提供稳定的导入入口，保留以下顶层模块：

- `modules.domain`；
- `modules.rdap`；
- `modules.suffix`；
- `modules.whois`。

其中 `modules.domain.Domain` 仍支持 `resolve_suffix()`，但内部已经委托给
`DomainNormalizer`，并同时填充 Punycode、IDN、Public Suffix 和可注册域名。新代码应
优先使用 `DomainNormalizer.normalize()` 和 `NormalizedDomain`。

旧版通用 `WhoisParser` 仍保留用于兼容和测试，但默认服务使用严格的
`ProfiledWhoisParser`。未知 ccTLD 不会交给通用正则静默解析。

以下是有意做出的 API 变化：

| 原 API | 新 API | 原因 |
| --- | --- | --- |
| `Suffix.resolve_idn()` | `DomainNormalizer.normalize()` | 标准化统一放到纯本地组件 |
| `Suffix.resolve_iana()` | `await IanaClient.discover()` | 网络行为不再放进 Pydantic 模型 |
| `modules.suffix.Suffix` | `RegistryEndpoint` | PSL 后缀与注册局端点是不同概念 |
| 同步 `query_whois()` | `await WhoisClient.query()` | 统一异步编排、超时和响应限制 |
| 通用 WHOIS 自动猜测 | `ProfiledWhoisParser` | 未知格式必须显式报告不支持 |

因此 `modules.suffix` 和 `modules.whois` 是新组件的集中导出入口，并不保证旧版 `Suffix`
和 `query_whois()` 调用形式继续可用。

## 12. 测试覆盖

测试位于：

- `tests/test_domain_lookup.py`；
- `tests/test_whois_profiles.py`。

覆盖内容包括：

- IDN 到 Punycode 的标准化；
- `co.uk` Public Suffix 与 IANA `uk` TLD 的区别；
- 所有协议缓存优先于网络访问；
- RDAP 失败后 WHOIS 回退；
- 原始报文缓存复用；
- 批量查询的 IANA 请求合并；
- WHOIS Profile 精确后缀匹配和 TLD 回退；
- Profile 替换、删除和冲突检测；
- `.cn` 注册域名与未注册响应解析；
- 注册局格式变化检测。

运行：

```powershell
.venv\Scripts\python.exe -m unittest discover -v
```

## 13. 当前限制与后续工作

1. 默认 WHOIS 注册表目前只提供 `.cn` 示例 Profile；其他 ccTLD 必须使用真实报文夹具
   逐个实现和验证。
2. `DomainLookupService` 的协议顺序目前默认是 `rdap → whois`；后续可增加
   `RegistryLookupPolicy`，让 gTLD 明确只走 RDAP、不同 ccTLD 使用各自策略。
3. WHOIS 未注册等负面响应已有结构化状态，但应用层尚未实现独立的负面结果模型；未来
   可增加负缓存，避免重复查询未注册域名。
4. RDAP Parser 目前覆盖项目需要的核心字段，不是完整 RFC 对象映射。
5. 内存缓存不跨进程、不持久化，生产环境必须替换为数据库实现。
6. PSL 快照需要独立更新机制。
7. 每个新增 WHOIS Profile 都应保存脱敏 fixture，并覆盖成功、未注册、限流、拒绝访问、
   字段缺失和格式变化场景。

## 14. 本次文件级修改清单

### 新增领域与响应模型

- `modules/models/domain.py`：新增标准化域名、注册商、生命周期、DNSSEC 和统一域名信息；
- `modules/models/registry.py`：新增注册局端点及新鲜度判断；
- `modules/models/response.py`：新增原始响应和查询结果模型。

### 新增标准化层

- `modules/normalization/domain.py`：集中处理输入清洗、IDNA、Punycode 和 PSL 提取；
- `modules/domain.py`：改为模型兼容导出，并让旧 `Domain.resolve_suffix()` 委托标准化器。

### 新增缓存层

- `modules/cache/base.py`：定义两个数据库无关的缓存抽象；
- `modules/cache/memory.py`：提供带异步锁的内存适配器。

### 新增网络客户端

- `modules/clients/base.py`：定义端点提供者和注册局查询客户端协议；
- `modules/clients/iana.py`：实现 IANA Root Database 和 RDAP Bootstrap；
- `modules/clients/rdap.py`：实现异步 RDAP 请求；
- `modules/clients/whois.py`：实现 WHOIS TCP 请求、超时、大小限制和 Profile 查询策略。

### 新增解析层

- `modules/parsers/base.py`：定义统一响应解析协议；
- `modules/parsers/rdap.py`：把 RDAP JSON 转为 `DomainInfo`；
- `modules/parsers/whois.py`：新增 Profile 路由入口，同时保留旧通用解析器。

### 新增 WHOIS Profile 框架

- `modules/whois_profiles/base.py`：定义 Profile、查询策略和解析器抽象；
- `modules/whois_profiles/registry.py`：实现 O(1) 索引、注册、替换、删除和冲突检查；
- `modules/whois_profiles/query.py`：实现标准 WHOIS 查询和多编码解码；
- `modules/whois_profiles/key_value.py`：实现声明式 Key-Value 字段解析；
- `modules/whois_profiles/models.py`：定义 WHOIS 响应状态和解析结果；
- `modules/whois_profiles/defaults.py`：构造共享默认注册表；
- `modules/whois_profiles/builtin/cn.py`：提供 `.cn` 示例 Profile。

### 新增应用服务

- `modules/services/domain_lookup.py`：实现缓存优先、端点发现、协议回退、批量查询和并发合并。

### 调整顶层导出

- `modules/rdap.py`：导出新的 RDAP Client/Parser；
- `modules/suffix.py`：导出 `IanaClient` 和 `RegistryEndpoint`；
- `modules/whois.py`：集中导出 WHOIS Client、Parser、Profile 和 Registry。

### 新增测试和文档

- `tests/test_domain_lookup.py`：验证标准化、缓存、IANA、回退和批量查询；
- `tests/test_whois_profiles.py`：验证 Profile 注册表、`.cn` 解析和格式变化；
- `README.md`：更新项目入口和快速使用方式；
- `docs/architecture.md`：记录本次架构和改动；
- `docs/whois-profiles.md`：记录 ccTLD 扩展流程；
- `docs/cache-backends.md`：记录数据库缓存实现契约。
