# RDAP 双来源到期判定设计

## 判定来源

域名生命周期只使用 RDAP 报文的可验证日期和存在性，不使用 EPP/RDAP 状态标签。查询先获取注册局 RDAP 报文，再从域名对象顶层 `links` 选择 `rel=related` 且 `type=application/rdap+json` 的 HTTPS 链接查询注册商 RDAP。

## 状态规则

| 条件 | 状态 |
| --- | --- |
| 注册局 RDAP 不存在 | `released` |
| 注册商到期日缺失 | `unknown` |
| 注册商到期日尚未来临 | `active` |
| 注册商到期日已过且注册局到期日仍在未来 | `grace_period` |
| 注册商到期日已过 | `expired` |

`registry_expires_at` 和 `registrar_expires_at` 必须分别持久化。现有 `expires_at` 是历史兼容字段，不能用于新的生命周期判断。
