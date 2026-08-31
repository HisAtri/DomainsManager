from __future__ import annotations

from dataclasses import dataclass
from html import escape
from importlib.resources import files
from string import Template
from typing import Any

_TEMPLATE_PACKAGE = "domainsmanager_api.email_templates"
_NOTIFICATION_TEMPLATE = "notification.html"
_VERIFICATION_TEMPLATE = "verification.html"

_EVENT_PRESENTATION = {
    "domain.expiration_warning": ("域名即将到期", "请及时处理域名续费，避免服务中断。"),
    "domain.status_changed": ("域名状态发生变化", "检测到域名监控信息发生变化。"),
    "domain.query_failed": ("域名查询失败", "本次域名信息查询未能完成，系统将按策略重试。"),
}


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


def load_email_template(name: str = _NOTIFICATION_TEMPLATE) -> Template:
    """Load a packaged email template by filename.

    Keeping templates as package resources makes this work both from the source
    tree and after the application is built into a wheel.
    """
    return Template(files(_TEMPLATE_PACKAGE).joinpath(name).read_text(encoding="utf-8"))


def render_notification_email(payload: dict[str, Any], *, site_name: str) -> RenderedEmail:
    event_type = str(payload.get("type", "notification"))
    title, summary = _EVENT_PRESENTATION.get(
        event_type, ("域名监控通知", "您有一条新的域名监控通知。")
    )
    data = payload.get("data")
    fields = data if isinstance(data, dict) else {}
    created_at = str(payload.get("created_at", "-"))
    details = _detail_rows({"事件类型": event_type, **fields})
    html = load_email_template().substitute(
        site_name=escape(site_name),
        title=escape(title),
        summary=escape(summary),
        details=details,
        created_at=escape(created_at),
    )
    text = "\n".join([title, summary, *(_text_rows({"事件类型": event_type, **fields})), f"生成时间：{created_at}"])
    return RenderedEmail(subject=f"{site_name}：{title}", text=text, html=html)


def render_verification_email(
    *, site_name: str, verification_url: str, expires_in_minutes: int = 30
) -> RenderedEmail:
    safe_site_name = escape(site_name)
    safe_url = escape(verification_url, quote=True)
    html = load_email_template(_VERIFICATION_TEMPLATE).substitute(
        site_name=safe_site_name,
        verification_url=safe_url,
        expires_in_minutes=expires_in_minutes,
    )
    text = "\n".join(
        [
            f"{site_name}：验证您的邮箱地址",
            "请打开以下链接完成邮箱验证。验证完成后，此邮箱才能接收域名通知。",
            verification_url,
            f"该链接将在 {expires_in_minutes} 分钟后失效；若非本人操作，请忽略此邮件。",
        ]
    )
    return RenderedEmail(subject=f"{site_name}：验证邮箱地址", text=text, html=html)


def _detail_rows(fields: dict[str, Any]) -> str:
    return "".join(
        "<tr>"
        f'<td style="width:38%;padding:11px 14px;border-bottom:1px solid #e2e8f0;font-size:13px;color:#64748b;">{escape(key)}</td>'
        f'<td style="padding:11px 14px;border-bottom:1px solid #e2e8f0;font-size:13px;color:#334155;word-break:break-word;">{escape(_format_value(value))}</td>'
        "</tr>"
        for key, value in fields.items()
    )


def _text_rows(fields: dict[str, Any]) -> list[str]:
    return [f"{key}：{_format_value(value)}" for key, value in fields.items()]


def _format_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "-"
    if value is None:
        return "-"
    return str(value)
