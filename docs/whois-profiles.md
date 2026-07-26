# ccTLD WHOIS Profile 扩展指南

## 1. 为什么使用 Profile

WHOIS 没有统一的响应格式。不同 ccTLD 可能在以下方面存在差异：

- 查询字符串格式；
- 查询字符编码；
- 响应字符编码；
- 字段名称和时间格式；
- 未注册、限流和拒绝访问提示；
- Key-Value、分块或本地化文本结构；
- 一个 TLD 下的不同 Public Suffix 由不同服务处理。

因此系统不使用大型 `if/elif`，而是让每个域名空间注册一个 `WhoisProfile`：

```python
WhoisProfile(
    key="cn",
    suffixes=("cn", "公司.cn", "网络.cn"),
    query_strategy=...,
    parser=...,
)
```

Profile 把“适用于哪些后缀”“如何查询”“如何解码”“如何解析”绑定在一起。

## 2. Profile 查找规则

`WhoisProfileRegistry.resolve(domain)` 按以下顺序查找：

1. `NormalizedDomain.public_suffix`；
2. `NormalizedDomain.tld`；
3. 都不存在时抛出 `UnsupportedWhoisProfileError`。

例如 `example.co.uk`：

- 优先查找 `co.uk` Profile；
- 如果没有，再查找 `uk` Profile。

注册表在写入时把 Unicode 后缀转换为 Punycode，并创建不可变字典快照。域名解析时只做
两次字典读取，不遍历全部 Profile，时间复杂度为 O(1)。

注册表支持并发读取和受锁保护的写入。每次新增、替换或删除都会增加 `generation`。

## 3. 添加 Key-Value 类型注册局

大多数结构接近下面形式的注册局可以使用 `KeyValueWhoisParser`：

```text
Domain Name: example.cc
Registrar: Example Registrar
Registered: 2020-01-01
Expires: 2030-01-01
Nameserver: ns1.example.cc
```

新建 `modules/whois_profiles/builtin/example_cc.py`：

```python
from modules.whois_profiles import (
    KeyValueWhoisParser,
    StandardWhoisQuery,
    WhoisFieldMap,
    WhoisProfile,
)


def create_example_profile() -> WhoisProfile:
    return WhoisProfile(
        key="example-cc",
        suffixes=("cc", "co.cc"),
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(
            key="example-cc",
            version="1",
            fields=WhoisFieldMap(
                domain=("Domain Name",),
                registrar=("Registrar",),
                registered_at=("Registered",),
                expires_at=("Expires",),
                nameserver=("Nameserver",),
                dnssec=("DNSSEC",),
            ),
            not_found_markers=("domain is available",),
            rate_limit_markers=("too many queries",),
            access_denied_markers=("access denied",),
            temporary_failure_markers=("service unavailable",),
        ),
    )
```

加入默认注册表：

```python
# modules/whois_profiles/defaults.py

def build_default_whois_registry() -> WhoisProfileRegistry:
    registry = WhoisProfileRegistry()
    registry.register(create_cn_profile())
    registry.register(create_example_profile())
    return registry
```

### 3.1 `WhoisFieldMap` 字段

| 属性 | `DomainInfo` 目标字段 |
| --- | --- |
| `domain` | 域名 |
| `handle` | Registry Handle |
| `registrar` | 注册商名称 |
| `registrar_id` | 注册商 IANA ID |
| `registrar_url` | 注册商 URL |
| `abuse_email` | Abuse Email |
| `abuse_phone` | Abuse Phone |
| `status` | 域名状态列表 |
| `registered_at` | 注册时间 |
| `expires_at` | 过期时间 |
| `updated_at` | 更新时间 |
| `nameserver` | 名称服务器列表 |
| `dnssec` | DNSSEC 状态 |

每个属性可以声明多个候选标签。解析器按顺序选择第一个存在的标签：

```python
expires_at=("Registry Expiry Date", "Expiration Date", "Expire")
```

## 4. 自定义时间或字段处理

如果主体仍是 Key-Value，只需继承并覆盖局部方法：

```python
class ExampleKeyValueParser(KeyValueWhoisParser):
    def parse_date(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.strptime(value, "%Y年%m月%d日")
```

还可以覆盖 `parse_dnssec()` 或最终的 `parse()`，但应保留：

- 响应状态分类；
- `parser_key`；
- `parser_version`；
- 无法解析字段的 Warning；
- 原始响应的 `source_url` 与 `fetched_at`。

## 5. 完全自定义响应解析器

分块、本地化或非 Key-Value 响应应直接实现 `WhoisResponseParser`：

```python
from modules.whois_profiles import (
    WhoisParseResult,
    WhoisResponseParser,
    WhoisResponseStatus,
)


class ExampleBlockParser(WhoisResponseParser):
    key = "example-block"
    version = "1"

    def parse(self, response, domain) -> WhoisParseResult:
        body = response.body

        if "No entries found" in body:
            return WhoisParseResult(
                status=WhoisResponseStatus.NOT_FOUND,
                parser_key=self.key,
                parser_version=self.version,
            )

        info = self._parse_blocks(body, domain)
        return WhoisParseResult(
            status=WhoisResponseStatus.FOUND,
            info=info,
            parser_key=self.key,
            parser_version=self.version,
        )
```

解析失败、未注册和格式变化不能都转换为空的 `DomainInfo`，必须返回准确状态。

## 6. 自定义查询与解码

默认 `StandardWhoisQuery` 发送：

```text
{registrable_domain}\r\n
```

并依次尝试 UTF-8、Latin-1 解码。

可以通过参数调整：

```python
strategy = StandardWhoisQuery(
    template="-T dn {domain}\r\n",
    response_encodings=("utf-8", "shift_jis"),
)
```

更复杂的查询应实现 `WhoisQueryStrategy`：

```python
class ExampleQueryStrategy(WhoisQueryStrategy):
    def build_query(self, domain: NormalizedDomain) -> bytes:
        return f"domain {domain.registrable_domain}\r\n".encode("ascii")

    def decode(self, response: bytes) -> str:
        return response.decode("specific-encoding", errors="replace")
```

网络连接、缓存和服务编排无需修改。

## 7. 应用级注册表注入

如果不希望修改默认注册表，可以创建应用自己的注册表：

```python
registry = WhoisProfileRegistry()
registry.register(create_cn_profile())
registry.register(create_example_profile())

whois_client = WhoisClient(profile_registry=registry)
whois_parser = ProfiledWhoisParser(registry=registry)

service = DomainLookupService(
    clients={
        "rdap": RdapClient(),
        "whois": whois_client,
    },
    parsers={
        "rdap": RdapParser(),
        "whois": whois_parser,
    },
)
```

客户端和解析器必须共享同一个注册表：客户端用它构造查询和解码，解析器用它解释响应。

## 8. 替换和删除 Profile

替换相同 key：

```python
registry.register(new_profile, replace=True)
```

替换时旧 Profile 不再使用的后缀会从索引中删除。

删除：

```python
registry.unregister("example-cc")
```

为避免配置被意外覆盖，即使 `replace=True`，也不能抢占另一个 key 已经拥有的后缀。需要
先显式删除原 Profile，再注册新的所有者。

## 9. 响应状态

`WhoisResponseStatus` 包含：

| 状态 | 含义 |
| --- | --- |
| `FOUND` | 找到已注册域名数据 |
| `NOT_FOUND` | 域名未注册或不存在 |
| `RATE_LIMITED` | 查询频率受限 |
| `ACCESS_DENIED` | 注册局拒绝访问 |
| `INVALID_QUERY` | 查询格式错误 |
| `TEMPORARY_FAILURE` | 服务临时不可用或空响应 |
| `UNKNOWN` | 无法识别报文结构 |

如果 `KeyValueWhoisParser` 没有发现任何已配置字段，会返回 `UNKNOWN`，而不是使用请求域名
伪造成功结果。这通常意味着注册局改变了报文格式，应立即检查 fixture 和解析规则。

## 10. 解析器版本管理

每个 Parser 必须声明稳定的 `key` 和 `version`：

```python
key="cn"
version="2"
```

以下情况应增加版本号：

- 字段映射变化；
- 时间解释规则变化；
- 状态归一化变化；
- 输出的 `DomainInfo` 语义变化。

数据库以后可以根据 `parser_key + parser_version` 判断是否需要使用已有原始报文重新解析。

## 11. Fixture 和测试要求

每个正式 Profile 都应保存脱敏的真实响应：

```text
tests/fixtures/whois/example_cc/
├── registered.txt
├── not_found.txt
├── rate_limited.txt
├── access_denied.txt
├── incomplete.txt
└── changed_format.txt
```

至少验证：

1. 正常字段映射；
2. 时间与时区；
3. 多个名称服务器和状态；
4. 未注册响应；
5. 限流与拒绝访问；
6. 字段缺失时的部分结果；
7. 编码；
8. 格式变化必须得到 `UNKNOWN` 或明确异常。

不要依赖在线 WHOIS 作为单元测试：在线服务会变化、限流且不可重复。在线查询只适合作为
受控的集成测试。

## 12. 当前内置 Profile

目前默认注册表只包含 `.cn` 示例，定义于：

```text
modules/whois_profiles/builtin/cn.py
```

它演示了 Unicode Public Suffix、CN 字段映射、未注册、限流和拒绝访问标记。其他 ccTLD
应基于对应注册局的真实报文逐个加入，不能假设 `.cn` 规则适用于其他后缀。
