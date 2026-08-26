from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domainsmanager_api.global_setting_registry import GLOBAL_SETTING_BY_KEY
from domainsmanager_api.secret_settings import decrypt_secret
from domainsmanager_api.settings import Settings
from domainsmanager_application.notifications import OutboxMessage
from domainsmanager_persistence.models import GlobalSetting

NOTIFICATION_CONFIGURATION_KEYS = frozenset(
    key for key, definition in GLOBAL_SETTING_BY_KEY.items()
    if definition.group in {"通知", "邮件投递"}
)


async def delivery_settings(
    defaults: Settings, sessions: async_sessionmaker[AsyncSession]
) -> Settings:
    async with sessions() as session:
        rows = {
            row.key: row.value
            for row in (
                await session.execute(
                    select(GlobalSetting).where(GlobalSetting.key.in_(NOTIFICATION_CONFIGURATION_KEYS))
                )
            ).scalars()
        }
    values: dict[str, object] = {}
    for key, raw in rows.items():
        definition = GLOBAL_SETTING_BY_KEY[key]
        if definition.secret:
            values[key] = SecretStr(decrypt_secret(raw, defaults.configuration_encryption_key))
        elif definition.kind == "boolean":
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
) -> None:
    settings = await delivery_settings(settings, sessions)
    if message.channel == "webhook":
        url = message.channel_config.get("webhook_url")
        if not isinstance(url, str):
            raise ValueError("webhook notification is missing a URL")
        async with httpx.AsyncClient(timeout=settings.notification_delivery_timeout_seconds) as client:
            response = await client.post(url, json=message.payload)
            response.raise_for_status()
        return
    if message.channel != "email" or not message.recipient_email:
        raise ValueError("email notification has no recipient")
    await asyncio.to_thread(_send_email, message, settings)


def _send_email(message: OutboxMessage, settings: Settings) -> None:
    if not settings.smtp_host or not settings.smtp_from:
        raise ValueError("SMTP is not configured")
    email = EmailMessage()
    email["From"], email["To"] = settings.smtp_from, message.recipient_email
    email["Subject"] = f"DomainsManager: {message.payload['event_type']}"
    email.set_content(str(message.payload))
    username = settings.smtp_username or settings.smtp_from
    client_factory = smtplib.SMTP_SSL if settings.smtp_encryption == "ssl_tls" else smtplib.SMTP
    with client_factory(settings.smtp_host, settings.smtp_port, timeout=settings.notification_delivery_timeout_seconds) as client:
        if settings.smtp_encryption == "starttls":
            client.starttls()
        if username and settings.smtp_password:
            client.login(username, settings.smtp_password.get_secret_value())
        client.send_message(email)
