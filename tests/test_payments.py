import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.enums import PaymentStatus
from app.models.outbox import Outbox
from app.payments import (
    IdempotencyConflict,
    apply_gateway_result,
    create_payment,
    fingerprint,
    try_claim_webhook,
    try_mark_webhook_sent,
)
from app.schemas.payment import PaymentCreate


def _payload(**overrides) -> PaymentCreate:
    data = {
        "amount": Decimal("150.50"),
        "currency": "RUB",
        "description": "заказ 42",
        "metadata": {"order_id": "42"},
        "webhook_url": "https://example.test/hook",
    }
    data.update(overrides)
    return PaymentCreate(**data)


def test_fingerprint_stable_and_sensitive_to_payload() -> None:
    base = fingerprint(Decimal("10.00"), "RUB", "a", {}, "https://x.test/")
    same = fingerprint(Decimal("10.0"), "RUB", "a", {}, "https://x.test/")
    other = fingerprint(Decimal("10.01"), "RUB", "a", {}, "https://x.test/")
    assert base == same
    assert base != other


async def test_create_payment_writes_outbox_in_same_commit(session) -> None:
    payment = await create_payment(session, _payload(), "order-1")
    outbox = (await session.scalars(select(Outbox))).all()
    assert payment.status == PaymentStatus.PENDING.value
    assert len(outbox) == 1
    assert outbox[0].aggregate_id == payment.id
    assert outbox[0].event_type == "payments.new"
    assert outbox[0].published_at is None
    assert outbox[0].payload == {"payment_id": str(payment.id)}


async def test_idempotent_replay_returns_same_payment_without_second_outbox(session) -> None:
    first = await create_payment(session, _payload(), "order-1")
    second = await create_payment(session, _payload(), "order-1")
    assert first.id == second.id
    assert len((await session.scalars(select(Outbox))).all()) == 1


async def test_idempotency_conflict_on_different_body(session) -> None:
    await create_payment(session, _payload(), "order-1")
    with pytest.raises(IdempotencyConflict):
        await create_payment(session, _payload(description="другой"), "order-1")


async def test_apply_gateway_result_does_not_overwrite_terminal_status(session) -> None:
    payment = await create_payment(session, _payload(), "order-1")
    first = await apply_gateway_result(session, payment.id, PaymentStatus.SUCCEEDED, None)
    second = await apply_gateway_result(
        session, payment.id, PaymentStatus.FAILED, "шлюз отклонил платёж"
    )
    await session.commit()
    assert first is not None
    assert second is not None
    assert second.status == PaymentStatus.SUCCEEDED.value
    assert second.failure_reason is None
    assert second.processed_at is not None


async def test_try_mark_webhook_sent_is_cas(session) -> None:
    payment = await create_payment(session, _payload(), "order-1")
    assert await try_mark_webhook_sent(session, payment.id)
    await session.commit()
    await session.refresh(payment)
    stamped = payment.webhook_sent_at
    assert stamped is not None
    assert not await try_mark_webhook_sent(session, payment.id)
    await session.commit()
    await session.refresh(payment)
    assert payment.webhook_sent_at == stamped


async def test_apply_gateway_result_missing_payment(session) -> None:
    assert await apply_gateway_result(session, uuid.uuid4(), PaymentStatus.SUCCEEDED, None) is None


async def test_webhook_claim_is_exclusive_until_stale(session) -> None:
    from datetime import datetime, timedelta, timezone

    from app.config import settings

    payment = await create_payment(session, _payload(), "order-1")
    assert await try_claim_webhook(session, payment.id)
    await session.commit()
    assert not await try_claim_webhook(session, payment.id)
    await session.commit()
    payment.webhook_claimed_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.webhook_claim_ttl_seconds + 1
    )
    await session.commit()
    assert await try_claim_webhook(session, payment.id)
    await session.commit()
