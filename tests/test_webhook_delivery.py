from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, ClassVar
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domainsmanager_api import notifier
from domainsmanager_api.schemas.notifications import CreateNotificationRuleRequest
from domainsmanager_api.settings import Settings
from domainsmanager_application.notifications import (
    NotificationDeliveryFailure,
    OutboxMessage,
)


class FakeResponseContext(AbstractAsyncContextManager):
    def __init__(self, status_code: int, headers: dict[str, str]) -> None:
        self.response = type(
            "HeaderOnlyResponse",
            (),
            {"status_code": status_code, "headers": headers},
        )()

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeClient(AbstractAsyncContextManager):
    status_code = 204
    response_headers: ClassVar[dict[str, str]] = {}
    init_kwargs: ClassVar[dict[str, Any]] = {}
    request: tuple[str, str, dict, dict] | None = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).init_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def stream(self, method: str, url: str, *, json: dict, headers: dict):
        type(self).request = method, url, json, headers
        return FakeResponseContext(self.status_code, self.response_headers)


def message() -> OutboxMessage:
    return OutboxMessage(
        id=uuid4(),
        lease_token=uuid4(),
        channel="webhook",
        channel_config={"webhook_url": "https://hooks.example.test/events"},
        payload={"id": str(uuid4()), "type": "domain.status_changed", "data": {}},
        attempt_count=1,
        recipient_email=None,
    )


@pytest.mark.asyncio
async def test_webhook_streams_headers_only_and_uses_only_admin_proxy(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        webhook_proxy_url="socks5://proxy.example.test:1080",
    )

    async def effective(*_args: object) -> Settings:
        return settings

    monkeypatch.setattr(notifier, "delivery_settings", effective)
    monkeypatch.setattr(notifier.httpx, "AsyncClient", FakeClient)
    FakeClient.status_code = 204
    FakeClient.response_headers = {"Content-Encoding": "gzip"}

    result = await notifier.deliver(message(), settings, object())

    assert result.outcome == "success"
    assert result.response_status_code == 204
    assert FakeClient.init_kwargs["proxy"] == "socks5://proxy.example.test:1080"
    assert FakeClient.init_kwargs["trust_env"] is False
    assert FakeClient.init_kwargs["verify"] is True
    assert FakeClient.init_kwargs["follow_redirects"] is False
    assert FakeClient.request is not None
    assert FakeClient.request[0] == "POST"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "outcome", "retryable", "message_text"),
    [
        (301, "redirect_rejected", False, "redirect"),
        (302, "redirect_rejected", False, "redirect"),
        (429, "rate_limited", True, "rate limited"),
        (404, "http_error", False, "4**"),
        (503, "http_error", True, "5**"),
    ],
)
async def test_webhook_statuses_are_classified_and_sanitized(
    monkeypatch, status: int, outcome: str, retryable: bool, message_text: str
) -> None:
    settings = Settings(_env_file=None)

    async def effective(*_args: object) -> Settings:
        return settings

    monkeypatch.setattr(notifier, "delivery_settings", effective)
    monkeypatch.setattr(notifier.httpx, "AsyncClient", FakeClient)
    FakeClient.status_code = status
    FakeClient.response_headers = {"Retry-After": "120"}

    with pytest.raises(NotificationDeliveryFailure) as raised:
        await notifier.deliver(message(), settings, object())

    assert raised.value.outcome == outcome
    assert raised.value.retryable is retryable
    assert raised.value.response_status_code == status
    assert message_text in str(raised.value)
    assert "hooks.example" not in str(raised.value)
    if status == 429:
        assert raised.value.retry_after is not None
        assert raised.value.retry_after.total_seconds() == 120


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.test/events",
        "https://hooks.example.test:8443/events",
        "https://user:pass@hooks.example.test/events",
        "https://hooks.example.test/events#fragment",
    ],
)
def test_webhook_rule_rejects_unsafe_endpoint_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        CreateNotificationRuleRequest.model_validate(
            {
                "event_type": "domain.status_changed",
                "channel": "webhook",
                "webhook_name": "Alerts",
                "webhook_url": url,
            }
        )
