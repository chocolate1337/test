from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.schemas.webhook import WebhookEvent
from app.webhook import WebhookError, deliver_webhook, webhook_backoff_seconds


def _event() -> WebhookEvent:
    now = datetime.now(timezone.utc)
    return WebhookEvent(
        payment_id="00000000-0000-0000-0000-000000000001",
        status="succeeded",
        amount=Decimal("1.00"),
        currency="RUB",
        description="x",
        metadata={},
        created_at=now,
        processed_at=now,
    )


def test_backoff_is_exponential() -> None:
    assert webhook_backoff_seconds(1) == 1
    assert webhook_backoff_seconds(2) == 2
    assert webhook_backoff_seconds(3) == 4


async def test_deliver_webhook_retries_then_raises() -> None:
    event = _event()
    request = httpx.Request("POST", "https://example.test/hook")
    failing = httpx.Response(500, request=request)

    with (
        patch("app.webhook.httpx.AsyncClient") as client_cls,
        patch("app.webhook.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        client = AsyncMock()
        client.post = AsyncMock(return_value=failing)
        client_cls.return_value.__aenter__.return_value = client
        with pytest.raises(WebhookError):
            await deliver_webhook("https://example.test/hook", event)

    assert client.post.await_count == 3
    assert sleep.await_count == 2
    assert sleep.await_args_list[0].args[0] == 1
    assert sleep.await_args_list[1].args[0] == 2


async def test_deliver_webhook_stops_after_success() -> None:
    event = _event()
    request = httpx.Request("POST", "https://example.test/hook")
    with patch("app.webhook.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = AsyncMock(return_value=httpx.Response(200, request=request))
        client_cls.return_value.__aenter__.return_value = client
        await deliver_webhook("https://example.test/hook", event)
    assert client.post.await_count == 1
