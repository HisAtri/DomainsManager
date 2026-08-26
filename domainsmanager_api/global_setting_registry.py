from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from domainsmanager_api.settings import Settings

ValueKind = Literal["integer", "number", "boolean", "string", "secret", "choice"]


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

    @property
    def secret(self) -> bool:
        return self.kind == "secret"

    def default(self, settings: Settings) -> int | float | bool | str | None:
        value = getattr(settings, self.key)
        return value.get_secret_value() if self.secret and value is not None else value


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
    hard_minimum = 0 if key in {"successful_refresh_ttl_seconds", "task_retry_base_seconds", "task_retry_max_seconds", "notification_retry_base_seconds", "notification_retry_max_seconds"} else 1
    hard_maximum = 65535 if key == "smtp_port" else None
    return GlobalSettingDefinition(key, group, label, description, "integer", hard_minimum, hard_maximum, unit, None, live)


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
    return GlobalSettingDefinition(key, group, label, description, "number", 0.001, None, unit, None, live)


def boolean(key: str, group: str, label: str, description: str, live: bool = True) -> GlobalSettingDefinition:
    return GlobalSettingDefinition(key, group, label, description, "boolean", live=live)


def string(key: str, group: str, label: str, description: str) -> GlobalSettingDefinition:
    return GlobalSettingDefinition(key, group, label, description, "string", live=True)


def secret(key: str, group: str, label: str, description: str) -> GlobalSettingDefinition:
    return GlobalSettingDefinition(key, group, label, description, "secret", live=True)


def choice(
    key: str,
    group: str,
    label: str,
    description: str,
    choices: tuple[str, ...],
) -> GlobalSettingDefinition:
    return GlobalSettingDefinition(key, group, label, description, "choice", choices=choices, live=True)


GLOBAL_SETTINGS = (
    boolean("registration_enabled", "账户与访问", "开放用户注册", "控制新用户是否可以自行创建账户。"),
    integer("check_interval_seconds", "域名监控", "常规检查周期", "域名成功检查后的下一次检查间隔。", 60, 2_592_000, unit="秒"),
    integer("successful_refresh_ttl_seconds", "域名监控", "成功结果缓存时长", "成功查询结果的缓存时长。", 60, 2_592_000, unit="秒"),
    integer("task_lease_seconds", "刷新任务", "任务租约时长", "Worker 领取刷新任务后的最长占用时间。", 30, 3600, unit="秒"),
    integer("task_max_attempts", "刷新任务", "最大重试次数", "刷新任务发生可恢复错误时的最多尝试次数。", 1, 100),
    integer("task_retry_base_seconds", "刷新任务", "重试基础延迟", "刷新任务首次重试前的等待时间。", 1, 3600, unit="秒"),
    integer("task_retry_max_seconds", "刷新任务", "重试最大延迟", "指数退避允许达到的最长等待时间。", 1, 86_400, unit="秒"),
    number("worker_poll_interval_seconds", "刷新任务", "Worker 轮询间隔", "没有可执行刷新任务时的等待时间。", 0.1, 60, unit="秒"),
    number("scheduler_poll_interval_seconds", "调度", "调度扫描间隔", "Scheduler 扫描到期域名的时间间隔。", 0.1, 300, unit="秒"),
    integer("scheduler_batch_size", "调度", "单次调度数量", "Scheduler 一次领取的最大域名数量。", 1, 1000),
    number("notification_delivery_timeout_seconds", "通知", "通知投递超时", "单次通知投递允许占用的最长时间。", 0.1, 120, unit="秒"),
    number("notification_worker_poll_interval_seconds", "通知", "通知 Worker 轮询间隔", "没有可投递通知时的等待时间。", 0.1, 60, unit="秒"),
    integer("notification_max_attempts", "通知", "通知最大重试次数", "通知投递失败后的最大尝试次数。", 1, 100),
    integer("notification_retry_base_seconds", "通知", "通知重试基础延迟", "通知首次重试前的等待时间。", 1, 3600, unit="秒"),
    integer("notification_retry_max_seconds", "通知", "通知重试最大延迟", "通知指数退避允许达到的最长等待时间。", 1, 86_400, unit="秒"),
    string("smtp_host", "邮件投递", "邮件服务器地址", ""),
    integer("smtp_port", "邮件投递", "SMTP服务器端口", "" , 1, 65535),
    choice("smtp_encryption", "邮件投递", "加密方式", "", ("none", "starttls", "ssl_tls")),
    string("smtp_from", "邮件投递", "发信邮箱", "包含@后缀的完整邮箱"),
    string("smtp_username", "邮件投递", "SMTP用户名", "通常与发信邮箱相同。部分邮局使用自定义用户名，在此设置"),
    secret("smtp_password", "邮件投递", "SMTP密码", ""),

)

GLOBAL_SETTING_BY_KEY = {definition.key: definition for definition in GLOBAL_SETTINGS}
