from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

import httpx

from domainsmanager_api.settings import Settings
from domainsmanager_application.notifications import OutboxMessage


async def deliver(message: OutboxMessage, settings: Settings) -> None:
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
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.notification_delivery_timeout_seconds) as client:
        if settings.smtp_starttls:
            client.starttls()
        if settings.smtp_username and settings.smtp_password:
            client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        client.send_message(email)
