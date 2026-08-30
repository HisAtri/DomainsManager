from __future__ import annotations

import asyncio
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domainsmanager_api.email_renderer import (
    render_notification_email,
    render_verification_email,
)
from domainsmanager_api.global_setting_registry import GLOBAL_SETTING_BY_KEY
from domainsmanager_api.settings import Settings
from domainsmanager_application.notifications import (
    NotificationDeliveryFailure,
    NotificationDeliveryResult,
    NotificationDeliverySuppressed,
    OutboxMessage,
)
from domainsmanager_persistence.models import GlobalSetting

NOTIFICATION_CONFIGURATION_KEYS = frozenset(
    key for key, definition in GLOBAL_SETTING_BY_KEY.items()
    if definition.group == "通知设置"
)
LEGACY_SMTP_KEYS = frozenset({"smtp_starttls"})


async def delivery_settings(
    defaults: Settings, sessions: async_sessionmaker[AsyncSession]
) -> Settings:
    async with sessions() as session:
        rows = {
            row.key: row.value
            for row in (
                await session.execute(
                    select(GlobalSetting).where(GlobalSetting.key.in_(NOTIFICATION_CONFIGURATION_KEYS | LEGACY_SMTP_KEYS))
                )
            ).scalars()
        }
    values: dict[str, object] = {}
    for key, raw in rows.items():
        definition = GLOBAL_SETTING_BY_KEY.get(key)
        if definition is None:
            if key == "smtp_starttls":
                values["smtp_encryption"] = "starttls" if raw == "true" else "none"
            continue
        if definition.kind == "boolean":
            values[key] = raw == "true"
        elif definition.kind == "integer":
            values[key] = int(raw)
        elif definition.kind == "number":
            values[key] = float(raw)
        elif definition.kind == "choice":
            values[key] = raw
        else:
            values[key] = raw or None
    return defaults.model_copy(update=values)


async def deliver(
    message: OutboxMessage,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
) -> NotificationDeliveryResult:
    settings = await delivery_settings(settings, sessions)
    if message.channel == "webhook":
        url = message.channel_config.get("webhook_url")
        if not isinstance(url, str):
            raise NotificationDeliveryFailure(
                "configuration_error", "Webhook endpoint is not configured", retryable=False
            )
        try:
            async with httpx.AsyncClient(
                proxy=settings.webhook_proxy_url or None,
                trust_env=False,
                verify=True,
                follow_redirects=False,
                timeout=settings.notification_delivery_timeout_seconds,
            ) as client, client.stream(
                "POST",
                url,
                json=message.payload,
                headers={"User-Agent": "DomainsManager-Webhooks/1.0"},
            ) as response:
                status_code = response.status_code
                retry_after = (
                    response.headers.get("Retry-After")
                    if status_code == 429
                    else None
                )
        except httpx.ProxyError as error:
            raise NotificationDeliveryFailure(
                "proxy_error", "Webhook proxy connection failed", retryable=True
            ) from error
        except httpx.TimeoutException as error:
            raise NotificationDeliveryFailure(
                "network_error", "Webhook connection timed out", retryable=True
            ) from error
        except httpx.ConnectError as error:
            if _caused_by_tls_error(error):
                raise NotificationDeliveryFailure(
                    "tls_error", "Webhook TLS verification failed", retryable=False
                ) from error
            raise NotificationDeliveryFailure(
                "network_error", "Webhook connection failed", retryable=True
            ) from error
        except (httpx.NetworkError, httpx.ProtocolError) as error:
            raise NotificationDeliveryFailure(
                "network_error", "Webhook connection was interrupted", retryable=True
            ) from error

        if 200 <= status_code < 300:
            return NotificationDeliveryResult("success", status_code)
        if status_code in {301, 302}:
            raise NotificationDeliveryFailure(
                "redirect_rejected", "Webhook redirect was rejected",
                response_status_code=status_code, retryable=False,
            )
        if status_code == 429:
            raise NotificationDeliveryFailure(
                "rate_limited", "Webhook endpoint rate limited the request",
                response_status_code=status_code, retryable=True,
                retry_after=_parse_retry_after(retry_after),
            )
        status_class = f"{status_code // 100}**"
        raise NotificationDeliveryFailure(
            "http_error", f"Webhook endpoint returned {status_class}",
            response_status_code=status_code,
            retryable=status_code == 408 or 500 <= status_code < 600,
        )
    if message.channel != "email" or not message.recipient_email:
        raise ValueError("email notification has no recipient")
    if not settings.smtp_enabled:
        raise NotificationDeliverySuppressed("SMTP service is disabled")
    site_name = await _site_name(sessions, settings)
    await asyncio.to_thread(_send_email, message, settings, site_name)
    return NotificationDeliveryResult("success")


async def send_verification_email(
    recipient: str,
    verification_url: str,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    effective = await delivery_settings(settings, sessions)
    if not effective.smtp_enabled:
        raise NotificationDeliverySuppressed("SMTP service is disabled")
    site_name = await _site_name(sessions, effective)
    rendered = render_verification_email(
        site_name=site_name, verification_url=verification_url
    )
    await asyncio.to_thread(
        _send_rendered_email,
        recipient,
        rendered.subject,
        rendered.text,
        rendered.html,
        effective,
    )


async def send_test_email(
    recipient: str, settings: Settings, sessions: async_sessionmaker[AsyncSession]
) -> None:
    effective = await delivery_settings(settings, sessions)
    if not effective.smtp_enabled:
        raise NotificationDeliverySuppressed("SMTP service is disabled")
    site_name = await _site_name(sessions, effective)
    rendered = render_notification_email(
        {"type": "email.test", "data": {"message": "SMTP configuration is working."}},
        site_name=site_name,
    )
    await asyncio.to_thread(
        _send_rendered_email,
        recipient,
        f"{site_name}：SMTP 测试邮件",
        rendered.text,
        rendered.html,
        effective,
    )


def _caused_by_tls_error(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _parse_retry_after(value: str | None) -> timedelta | None:
    if not value:
        return None
    try:
        return timedelta(seconds=max(0, int(value)))
    except ValueError:
        try:
            at = parsedate_to_datetime(value)
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            return max(at - datetime.now(UTC), timedelta(0))
        except (TypeError, ValueError, OverflowError):
            return None


async def _site_name(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> str:
    definition = GLOBAL_SETTING_BY_KEY["site_name"]
    async with sessions() as session:
        configured = await session.scalar(
            select(GlobalSetting.value).where(GlobalSetting.key == definition.key)
        )
    return configured.strip() if configured and configured.strip() else str(definition.default(settings))


def _send_email(message: OutboxMessage, settings: Settings, site_name: str) -> None:
    rendered = render_notification_email(message.payload, site_name=site_name)
    _send_rendered_email(
        message.recipient_email, rendered.subject, rendered.text, rendered.html, settings
    )


def _send_rendered_email(
    recipient: str,
    subject: str,
    text: str,
    html: str,
    settings: Settings,
) -> None:
    if not settings.smtp_host or not settings.smtp_from:
        raise ValueError("SMTP is not configured")
    email = EmailMessage()
    email["From"], email["To"] = settings.smtp_from, recipient
    email["Subject"] = subject
    email.set_content(text)
    email.add_alternative(html, subtype="html")
    username = settings.smtp_username or settings.smtp_from
    client_factory = smtplib.SMTP_SSL if settings.smtp_encryption == "ssl_tls" else smtplib.SMTP
    with client_factory(settings.smtp_host, settings.smtp_port, timeout=settings.notification_delivery_timeout_seconds) as client:
        if settings.smtp_encryption == "starttls":
            client.starttls()
        if username and settings.smtp_password:
            client.login(username, settings.smtp_password)
        client.send_message(email)
