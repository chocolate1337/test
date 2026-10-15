from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.outbox import Outbox
from app.outbox import publish_one
from app.payments import create_payment
from app.schemas.payment import PaymentCreate


def _create_body() -> PaymentCreate:
    return PaymentCreate(
        amount="10.00",
        description="x",
        webhook_url="https://example.test/hook",
    )


async def test_publish_one_marks_published_only_after_broker_ack(session) -> None:
    payment = await create_payment(session, _create_body(), "k1")
    broker = AsyncMock()
    assert await publish_one(session, broker)
    broker.publish.assert_awaited_once()
    kwargs = broker.publish.await_args.kwargs
    assert kwargs["correlation_id"] == str(payment.id)
    assert kwargs["persist"] is True

    rows = (await session.scalars(select(Outbox))).all()
    assert len(rows) == 1
    assert rows[0].published_at is not None
    assert rows[0].payload == {"payment_id": str(payment.id)}


async def test_publish_one_does_not_mark_when_broker_fails(session) -> None:
    await create_payment(session, _create_body(), "k1")
    broker = AsyncMock()
    broker.publish.side_effect = RuntimeError("amqp down")
    with pytest.raises(RuntimeError):
        await publish_one(session, broker)

    await session.rollback()
    rows = (await session.scalars(select(Outbox))).all()
    assert rows[0].published_at is None


async def test_publish_one_returns_false_when_empty(session) -> None:
    broker = AsyncMock()
    assert await publish_one(session, broker) is False
    broker.publish.assert_not_awaited()
