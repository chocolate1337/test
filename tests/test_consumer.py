import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.consumer import retry_or_dead_letter, settle_failure
from app.process import PaymentNotFoundError, WebhookInFlightError
from app.retry import RETRY_HEADER
from app.schemas.events import PaymentNewEvent


def _msg(headers: dict | None = None) -> AsyncMock:
    msg = AsyncMock()
    msg.headers = headers or {}
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()
    msg.nack = AsyncMock()
    return msg


async def test_retry_publishes_to_ttl_queue_and_acks_original() -> None:
    event = PaymentNewEvent(payment_id=uuid.uuid4())
    msg = _msg()
    with patch("app.consumer.broker.publish", new_callable=AsyncMock) as publish:
        await retry_or_dead_letter(event, msg, attempt=1)
    publish.assert_awaited_once()
    kwargs = publish.await_args.kwargs
    assert kwargs["queue"] == "payments.new.retry.1000ms"
    assert kwargs["headers"][RETRY_HEADER] == 1
    assert kwargs["persist"] is True
    msg.ack.assert_awaited_once()
    msg.reject.assert_not_awaited()


async def test_third_failure_rejects_without_requeue() -> None:
    event = PaymentNewEvent(payment_id=uuid.uuid4())
    msg = _msg()
    with patch("app.consumer.broker.publish", new_callable=AsyncMock) as publish:
        await retry_or_dead_letter(event, msg, attempt=3)
    publish.assert_not_awaited()
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()


async def test_retry_publish_failure_nacks_for_redelivery() -> None:
    event = PaymentNewEvent(payment_id=uuid.uuid4())
    msg = _msg()
    with patch("app.consumer.broker.publish", new_callable=AsyncMock, side_effect=RuntimeError("down")):
        with pytest.raises(RuntimeError):
            await retry_or_dead_letter(event, msg, attempt=1)
    msg.nack.assert_awaited_once_with(requeue=True)
    msg.ack.assert_not_awaited()


async def test_missing_payment_goes_to_dlq_without_retry() -> None:
    event = PaymentNewEvent(payment_id=uuid.uuid4())
    msg = _msg()
    with patch("app.consumer.broker.publish", new_callable=AsyncMock) as publish:
        await settle_failure(event, msg, 1, PaymentNotFoundError(str(event.payment_id)))
    publish.assert_not_awaited()
    msg.reject.assert_awaited_once_with(requeue=False)


async def test_inflight_webhook_nacks_without_consuming_retry_budget(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "webhook_inflight_nack_delay_seconds", 0)
    event = PaymentNewEvent(payment_id=uuid.uuid4())
    msg = _msg()
    with patch("app.consumer.broker.publish", new_callable=AsyncMock) as publish:
        await settle_failure(event, msg, 1, WebhookInFlightError("busy"))
    publish.assert_not_awaited()
    msg.nack.assert_awaited_once_with(requeue=True)
    msg.reject.assert_not_awaited()
