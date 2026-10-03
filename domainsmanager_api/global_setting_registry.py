from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from domainsmanager_api.settings import Settings

ValueKind = Literal["integer", "number", "boolean", "string", "choice", "json"]
_MISSING = object()


@dataclass(frozen=True, slots=True)
class GlobalSettingDefinition:
    key: str
    group: str
    label: str
    description: str
    kind: ValueKind
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None
    choices: tuple[str, ...] | None = None
    live: bool = False
    editor: str = "input"
    language: str | None = None
    placeholder: str | None = None
    default_value: Any = _MISSING

    @property
    def uses_registry_default(self) -> bool:
        return self.default_value is not _MISSING

    def default(self, settings: Settings) -> Any:
        if self.default_value is not _MISSING:
            return self.default_value
        value = getattr(settings, self.key)
        return value


def integer(
    key: str,
    group: str,
    label: str,
    description: str,
    minimum: int,
    maximum: int,
    live: bool = True,
    unit: str | None = None,
) -> GlobalSettingDefinition:
    hard_minimum = (
        0
        if key
        in {
            "successful_refresh_ttl_seconds",
            "task_retry_base_seconds",
            "task_retry_max_seconds",
            "notification_retry_base_seconds",
            "notification_retry_max_seconds",
        }
        else 1
    )
    hard_maximum = 65535 if key == "smtp_port" else None
    return GlobalSettingDefinition(
        key,
        group,
        label,
        description,
        "integer",
        hard_minimum,
        hard_maximum,
        unit,
        None,
        live,
    )


def number(
    key: str,
    group: str,
    label: str,
    description: str,
    minimum: float,
    maximum: float,
    live: bool = True,
    unit: str | None = None,
) -> GlobalSettingDefinition:
    return GlobalSettingDefinition(
        key, group, label, description, "number", 0.001, None, unit, None, live
    )


def boolean(
    key: str, group: str, label: str, description: str, live: bool = True
) -> GlobalSettingDefinition:
    return GlobalSettingDefinition(key, group, label, description, "boolean", live=live)


def string(
    key: str, group: str, label: str, description: str
) -> GlobalSettingDefinition:
    return GlobalSettingDefinition(key, group, label, description, "string", live=True)


def choice(
    key: str,
    group: str,
    label: str,
    description: str,
    choices: tuple[str, ...],
) -> GlobalSettingDefinition:
    return GlobalSettingDefinition(
        key, group, label, description, "choice", choices=choices, live=True
    )


def site_setting(
    key: str,
    group: str,
    label: str,
    description: str,
    *,
    editor: str = "input",
    language: str | None = None,
    placeholder: str | None = None,
    default: Any = "",
) -> GlobalSettingDefinition:
    kind: ValueKind = "json" if editor == "links" else "string"
    return GlobalSettingDefinition(
        key,
        group,
        label,
        description,
        kind,
        live=True,
        editor=editor,
        language=language,
        placeholder=placeholder,
        default_value=default,
    )


GLOBAL_SETTINGS = (
    GlobalSettingDefinition(
        "github_enabled",
        "第三方登录",
        "启用 GitHub 登录",
        "允许用户使用 GitHub 账号登录本站。",
        "boolean",
        live=True,
        default_value=True,
    ),
    GlobalSettingDefinition(
        "linuxdo_enabled",
        "第三方登录",
        "启用 LinuxDo 登录",
        "允许用户使用 LinuxDo 账号登录本站。",
        "boolean",
        live=True,
        default_value=True,
    ),
    site_setting(
        "github_client_id",
        "第三方登录",
        "Client ID",
        "GitHub OAuth App 的客户端 ID。",
    ),
    site_setting(
        "github_client_secret",
        "第三方登录",
        "Client Secret",
        "GitHub OAuth App 的客户端密钥。",
    ),
    site_setting(
        "linuxdo_client_id",
        "第三方登录",
        "Client ID",
        "LinuxDo Connect 的客户端 ID。",
    ),
    site_setting(
        "linuxdo_client_secret",
        "第三方登录",
        "Client Secret",
        "LinuxDo Connect 的客户端密钥。",
    ),
    GlobalSettingDefinition(
        "oauth_attempt_ttl_seconds",
        "第三方登录",
        "第三方登录有效期",
        "在第三方页面完成授权的最长时间，超时后需要重新登录。",
        "integer",
        minimum=60,
        maximum=3600,
        unit="秒",
        live=True,
        default_value=600,
    ),
    choice(
        "anti_bot_mode",
        "安全设置",
        "人机验证方式",
        "决定登录、注册、添加域名与刷新时是否要求人机验证。",
        ("disabled", "image_captcha", "turnstile"),
    ),
    boolean(
        "captcha_rotate",
        "安全设置",
        "字符旋转",
        "验证码字符随机旋转，提高自动识别难度。",
    ),
    boolean(
        "captcha_offset",
        "安全设置",
        "字符偏移",
        "验证码字符随机错位，提高自动识别难度。",
    ),
    boolean(
        "captcha_warp", "安全设置", "字符形变", "验证码字符轻微变形，提高自动识别难度。"
    ),
    choice(
        "pow_difficulty",
        "安全设置",
        "验证强度",
        "验证计算所需时间：简单最快，困难最慢但拦截自动化程序更有效。",
        ("easy", "medium", "hard"),
    ),
    string(
        "turnstile_site_key",
        "安全设置",
        "Turnstile 站点密钥",
        "在 Cloudflare 控制台创建 Turnstile 站点后获得，用于在前台展示验证组件。",
    ),
    string(
        "turnstile_secret_key",
        "安全设置",
        "Turnstile 私密密钥",
        "用于服务端校验验证结果，请勿公开。",
    ),
    boolean(
        "registration_enabled",
        "账户与访问",
        "开放用户注册",
        "控制新用户是否可以自行创建账户。",
    ),
    boolean(
        "email_verification_enabled",
        "账户与访问",
        "启用邮箱验证",
        "开启后，注册和修改邮箱都需要通过邮件链接验证后才会生效。",
    ),
    string(
        "email_domain_allowlist",
        "账户与访问",
        "邮箱后缀白名单",
        "每行一个邮箱后缀，例如 @gmail.com；留空表示不限制。",
    ),
    integer(
        "normal_rate_limit_attempts",
        "操作频率限制",
        "日常操作次数上限",
        "每位用户在统计周期内可执行的日常操作次数（浏览、查看与修改设置等）。",
        1,
        10_000,
    ),
    integer(
        "normal_rate_limit_window_seconds",
        "操作频率限制",
        "日常操作统计周期",
        "统计日常操作次数的周期长度。",
        1,
        86_400,
        unit="秒",
    ),
    integer(
        "expensive_rate_limit_attempts",
        "操作频率限制",
        "添加域名与刷新次数上限",
        "统计周期内每位用户可执行的添加域名与手动刷新次数。",
        1,
        10_000,
    ),
    integer(
        "expensive_rate_limit_window_seconds",
        "操作频率限制",
        "添加域名与刷新统计周期",
        "统计添加域名与刷新次数的周期长度。",
        1,
        86_400,
        unit="秒",
    ),
    integer(
        "check_interval_seconds",
        "域名监控",
        "自动检查周期",
        "开启监控的域名多久自动查询一次。",
        60,
        2_592_000,
        unit="秒",
    ),
    integer(
        "successful_refresh_ttl_seconds",
        "域名监控",
        "刷新结果复用时长",
        "刷新成功后，该时长内再次刷新会直接显示已有结果，避免频繁查询注册局。",
        60,
        2_592_000,
        unit="秒",
    ),
    integer(
        "task_lease_seconds",
        "刷新任务",
        "单次刷新最长处理时间",
        "一次刷新超过该时长仍未完成，会被判定为超时并自动重新排队。",
        30,
        3600,
        unit="秒",
    ),
    integer(
        "task_max_attempts",
        "刷新任务",
        "最大重试次数",
        "刷新因网络或注册局临时故障失败时，最多自动重试的次数。",
        1,
        100,
    ),
    integer(
        "task_retry_base_seconds",
        "刷新任务",
        "首次重试等待时间",
        "刷新失败后等待多久进行第一次重试。",
        1,
        3600,
        unit="秒",
    ),
    integer(
        "task_retry_max_seconds",
        "刷新任务",
        "重试最长等待时间",
        "连续失败时，两次重试之间的最长间隔。",
        1,
        86_400,
        unit="秒",
    ),
    number(
        "worker_poll_interval_seconds",
        "刷新任务",
        "刷新请求响应间隔",
        "空闲时检查新刷新请求的间隔；值越小响应越快，系统开销略增。",
        0.1,
        60,
        unit="秒",
    ),
    number(
        "scheduler_poll_interval_seconds",
        "自动检查调度",
        "自动检查间隔",
        "多久检查一次已到期的域名；值越小越准时。",
        0.1,
        300,
        unit="秒",
    ),
    integer(
        "scheduler_batch_size",
        "自动检查调度",
        "每轮自动检查数量",
        "每轮最多安排多少个到期域名；值越大，集中到期时处理越快。",
        1,
        1000,
    ),
    number(
        "notification_delivery_timeout_seconds",
        "通知设置",
        "通知发送超时",
        "单条通知发送的最长时间，超时记为失败并自动重试。",
        0.1,
        120,
        unit="秒",
    ),
    number(
        "notification_worker_poll_interval_seconds",
        "通知设置",
        "通知发送间隔",
        "空闲时检查待发通知的间隔；值越小提醒越及时。",
        0.1,
        60,
        unit="秒",
    ),
    integer(
        "notification_max_attempts",
        "通知设置",
        "通知最大重试次数",
        "通知发送失败后最多自动重试的次数。",
        1,
        100,
    ),
    integer(
        "notification_retry_base_seconds",
        "通知设置",
        "通知首次重试等待时间",
        "通知发送失败后等待多久进行第一次重试。",
        1,
        3600,
        unit="秒",
    ),
    integer(
        "notification_retry_max_seconds",
        "通知设置",
        "通知重试最长等待时间",
        "连续失败时，两次重发之间的最长间隔。",
        1,
        86_400,
        unit="秒",
    ),
    string(
        "webhook_proxy_url",
        "通知设置",
        "Webhook 代理地址",
        "仅用于 Webhook 通知；支持 http:// 与 socks5://，留空表示直连。",
    ),
    boolean(
        "smtp_enabled",
        "通知设置",
        "启用 SMTP 邮件服务",
        "关闭后不会发送邮件通知",
    ),
    string(
        "smtp_host",
        "通知设置",
        "邮件服务器地址",
        "由邮件服务商提供，例如 smtp.example.com。",
    ),
    integer(
        "smtp_port",
        "通知设置",
        "SMTP服务器端口",
        "由邮件服务商提供；选择加密方式后会自动填入常用端口。",
        1,
        65535,
    ),
    choice(
        "smtp_encryption",
        "通知设置",
        "加密方式",
        "需与邮件服务商的要求一致，选错会导致发送失败。",
        ("none", "starttls", "ssl_tls"),
    ),
    string(
        "smtp_from",
        "通知设置",
        "发信邮箱",
        "通知邮件的发件地址，需为完整邮箱地址（含 @ 与域名）。",
    ),
    string(
        "smtp_username",
        "通知设置",
        "SMTP用户名",
        "通常与发信邮箱相同。部分邮局使用自定义用户名，在此设置",
    ),
    string(
        "smtp_password",
        "通知设置",
        "SMTP密码",
        "邮箱的 SMTP 授权码或密码，保存后不会回显。",
    ),
    site_setting(
        "site_name",
        "站点信息",
        "站点名称",
        "用于浏览器标题和页面左上角显示。",
        default="DomainsManager",
    ),
    site_setting(
        "site_url",
        "站点信息",
        "站点地址",
        "站点对外访问地址，用于生成邮件中的验证链接；需与用户实际访问的地址一致。",
        placeholder="https://console.example.com",
    ),
    site_setting(
        "site_logo",
        "站点信息",
        "站点 Logo",
        "输入图片 URL、站内路径或 SVG 代码。",
        editor="asset",
        placeholder="/default.svg 或 <svg>...</svg>",
        default="/default.svg",
    ),
    site_setting(
        "site_favicon",
        "站点信息",
        "站点 Favicon",
        "输入图片 URL、站内路径或 SVG 代码。",
        editor="asset",
        placeholder="/default.svg 或 <svg>...</svg>",
        default="/default.svg",
    ),
    site_setting(
        "footer_links",
        "页面配置",
        "页脚链接",
        "显示在页面底部的链接列表。",
        editor="links",
        default=[],
    ),
    site_setting(
        "footer_copyright",
        "页面配置",
        "版权信息",
        "显示在页面底部的版权文本。",
        editor="textarea",
        placeholder="© 2026 DomainsManager",
    ),
    site_setting(
        "icp_number",
        "页面配置",
        "ICP 备案号",
        "显示在页脚并链接至工信部备案查询。仅填写数字时会格式化为 ICP备xxxx号。",
        placeholder="京ICP备12345678号",
    ),
    site_setting(
        "police_record_number",
        "页面配置",
        "公安备案号",
        "显示在页脚并链接至公安备案查询。仅填写数字时会格式化为 公网安备 xxxx号。",
        placeholder="京公网安备11010802000000号",
    ),
    site_setting(
        "custom_css",
        "页面配置",
        "自定义 CSS",
        "追加到所有前台页面的 CSS，用于调整外观。",
        editor="code",
        language="css",
        placeholder="/* 自定义样式 */",
    ),
    site_setting(
        "custom_javascript",
        "页面配置",
        "自定义 JavaScript",
        "在所有前台页面加载完成后执行，用于接入统计或第三方组件。",
        editor="code",
        language="javascript",
        placeholder="// 自定义脚本",
    ),
    site_setting(
        "head_html",
        "页面配置",
        "头部 HTML",
        "插入每个前台页面 head 中的 HTML，例如站点验证 meta 标签。",
        editor="code",
        language="html",
        placeholder='<meta name="...">',
    ),
    site_setting(
        "body_end_html",
        "页面配置",
        "底部 HTML",
        "插入每个前台页面末尾的 HTML。",
        editor="code",
        language="html",
        placeholder="<div>...</div>",
    ),
    site_setting(
        "analytics_code",
        "页面配置",
        "网站统计代码",
        "粘贴统计服务提供的代码，将在所有前台页面加载时执行。",
        editor="code",
        language="javascript",
        placeholder="// analytics",
    ),
)

GLOBAL_SETTING_BY_KEY = {definition.key: definition for definition in GLOBAL_SETTINGS}
SITE_SETTINGS = tuple(
    definition
    for definition in GLOBAL_SETTINGS
    if definition.group.startswith("站点信息")
    or definition.group.startswith("页面配置")
)
