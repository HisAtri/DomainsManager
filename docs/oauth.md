# OAuth 登录部署与扩展

首期支持 GitHub OAuth App 与 LINUX DO Connect。第三方令牌仅用于获取身份，登录会话由本系统签发。账号按提供商和稳定用户 ID 识别；邮箱、用户名、昵称不会触发账号自动合并。

## 部署

1. 停止旧版服务并备份数据库，在项目根目录运行 `.venv\Scripts\python.exe -c "import asyncio; from domainsmanager_api.settings import Settings; from domainsmanager_persistence.db import run_migrations; asyncio.run(run_migrations(Settings().database_config()))"`。此命令读取当前 `.env` 对应的数据库；直接执行 `alembic upgrade head` 不会自动读取 `.env`。不要在旧版本程序仍运行时开放 OAuth。
2. 在 GitHub Developer settings 创建 OAuth App；在 LINUX DO Connect 申请应用。开发和生产环境分别登记应用，填写各自完整回调地址。
3. 在系统设置的“站点信息”中填写浏览器访问站点的源地址，例如 `https://domains.example.com`。本地开发填写 `http://localhost:5173`，Vite 代理 `/api` 到后端。
4. 在系统设置的“第三方登录”中分别填写 GitHub 和 LinuxDo 的 Client ID、Client Secret。凭据以明文保存于数据库，完整填写一组后该接入才启用；不需要 OAuth 专属 `.env` 项或加密密钥。
5. 生产 HTTPS 同时设置 `DOMAINSMANAGER_REFRESH_COOKIE_SECURE=true`，建议 refresh cookie 使用 `lax`。修改系统设置后无需重启 API。

默认回调地址：

| 提供商 | 回调 |
| --- | --- |
| GitHub | `https://domains.example.com/api/v1/auth/oauth2/github/callback` |
| LinuxDo | `https://domains.example.com/api/v1/auth/oauth2/linuxdo/callback` |

`api_prefix` 自定义时同步修改登记地址。回跳地址不使用请求 Host 或用户提交的 URL。不要在代理、应用或 APM 中记录回调查询参数、令牌响应、Cookie 或 Authorization。

未配置客户端时 OAuth 默认关闭。客户端只看到 GitHub 和 LinuxDo 两种固定接入及各自路径，不接受任意 provider 参数。移除客户端配置不会删除绑定数据；恢复配置后可继续使用。停用提供商前，应让仅依赖该提供商的用户设置密码或绑定其他提供商。

## 账号行为

- 管理员可用 GitHub、LinuxDo 各自的开关禁用登录，保留凭据和既有关联；开关开启且凭据完整时才出现登录入口。
- 新 OAuth 用户首次登录后进入用户名引导，按本站规则设置一次用户名；刷新或重登可继续，名称冲突不消耗机会，提交成功后不能再次改名。迁移前的已有用户不受影响。

- 首次 OAuth 登录遵循运行中的注册开关；关闭注册仍允许已有身份登录和已有账号绑定。
- 已有本地用户先用原方式登录，再到个人设置绑定，避免创建独立账号。跨账号合并和身份转移不提供自动流程。
- 每个本地用户每个提供商最多绑定一个身份。被其他用户绑定的身份不能抢占。
- 绑定、解绑和首次设置密码要求登录会话创建于最近十分钟；刷新 token 不延长该时间。超过时间需退出重新登录。
- OAuth-only 用户没有可用本地密码；首次设置后可使用页面显示的本地用户名登录。解绑时至少保留本地密码或一个已启用提供商的身份。解绑和设密会撤销其他会话。
- OAuth 不采集邮箱，不伪造邮箱或邮箱验证状态。第三方身份登录独立于密码注册表单的邮箱要求；需要邮件通知时由用户在个人设置添加并按站点策略验证邮箱。
- LinuxDo `active=false` 拒绝本次授权；信任等级和禁言状态不修改本站权限。本站封禁对所有登录方式生效。外部状态只在用户再次 OAuth 授权时检查，不定期轮询。
- 第三方授权采用浏览器跳转及单次 state 校验，首期不复用密码表单验证码；匿名入口和回调使用 expensive 限流策略。

## 新增提供商

新增提供商需新增独立适配器和明确的 API、前端入口；内部可复用授权状态和本地身份逻辑。输出 `ExternalIdentity`，其中 `subject` 必须是提供商保证稳定的 ID（未来 OIDC 使用验证后的 subject，并为不同 issuer 分配不同 provider key）。provider key 一旦使用应保持稳定。

身份表不需要新增列或数据库枚举，但具体授权 URI、token 请求和用户响应解析均属于各自适配器。GitHub 使用 S256，LinuxDo 使用机密客户端授权码流程；客户端设置仅包含各平台凭据，URI 和字段映射由代码确定。

`profile_json` 仅保存适配器白名单字段，禁止完整保存上游报文。LinuxDo `api_key`、`external_ids`、第三方 token 均不落库。将来需要持续调用第三方 API 时，另建授权凭据表保存加密 token、scope、过期和撤销状态，与身份表分开维护。

## 验证与回滚

使用测试应用分别验证允许授权、取消、首次开户、绑定、退出重登及解绑。在完成真实授权验证前保持客户端配置为空。离线自动化测试使用模拟 HTTP，不需要生产凭证。

代码回滚前先关闭 OAuth。迁移降级会删除外部身份与未完成授权记录，OAuth-only 用户无法再用原外部身份登录；应优先保留新 schema 只关闭功能。确需降级时先保证用户已设置密码，并保留数据库备份用于恢复。

接口依据：[GitHub OAuth](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)、[GitHub 用户 API](https://docs.github.com/en/rest/users/users)、[LINUX DO Connect](https://linux.do/t/topic/32752)。
