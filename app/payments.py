import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.enums import PaymentStatus
from app.models.outbox import Outbox
from app.models.payment import Payment
from app.schemas.events import PaymentNewEvent
from app.schemas.payment import KOPECKS, PaymentCreate, PaymentOut

logger = logging.getLogger(__name__)


class IdempotencyConflict(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"ключ {key} уже занят другим запросом")
        self.key = key


def to_out(payment: Payment) -> PaymentOut:
    return PaymentOut.model_validate(payment)


def fingerprint(
    amount: Decimal,
    currency: str,
    description: str,
    metadata: dict[str, Any],
    webhook_url: str,
) -> str:
    body = json.dumps(
        {
            "amount": str(amount.quantize(KOPECKS)),
            "currency": currency,
            "description": description,
            "metadata": metadata,
            "webhook_url": webhook_url,
        },
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


async def get_payment(session: AsyncSession, payment_id: uuid.UUID) -> Payment | None:
    return await session.get(Payment, payment_id)


async def lock_payment(session: AsyncSession, payment_id: uuid.UUID) -> Payment | None:
    result = await session.execute(select(Payment).where(Payment.id == payment_id).with_for_update())
    return result.scalar_one_or_none()


async def apply_gateway_result(
    session: AsyncSession,
    payment_id: uuid.UUID,
    status: PaymentStatus,
    reason: str | None,
) -> Payment | None:
    locked = await lock_payment(session, payment_id)
    if locked is None:
        return None
    if locked.status == PaymentStatus.PENDING.value:
        locked.status = status.value
        locked.failure_reason = reason
        locked.processed_at = datetime.now(timezone.utc)
    return locked


async def try_claim_webhook(session: AsyncSession, payment_id: uuid.UUID) -> bool:
    """Право отправить webhook: свободно или claim протух (крэш воркера)."""
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=settings.webhook_claim_ttl_seconds)
    result = await session.execute(
        update(Payment)
        .where(
            Payment.id == payment_id,
            Payment.webhook_sent_at.is_(None),
            or_(
                Payment.webhook_claimed_at.is_(None),
                Payment.webhook_claimed_at <= stale_before,
            ),
        )
        .values(webhook_claimed_at=now)
    )
    return result.rowcount > 0


async def release_webhook_claim(session: AsyncSession, payment_id: uuid.UUID) -> None:
    await session.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.webhook_sent_at.is_(None))
        .values(webhook_claimed_at=None)
    )


async def try_mark_webhook_sent(session: AsyncSession, payment_id: uuid.UUID) -> bool:
    result = await session.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.webhook_sent_at.is_(None))
        .values(webhook_sent_at=datetime.now(timezone.utc))
    )
    return result.rowcount > 0


async def create_payment(
    session: AsyncSession,
    data: PaymentCreate,
    idempotency_key: str,
) -> Payment:
    amount = data.amount.quantize(KOPECKS)
    digest = fingerprint(
        amount=amount,
        currency=data.currency.value,
        description=data.description,
        metadata=data.metadata,
        webhook_url=str(data.webhook_url),
    )

    existing = await session.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.request_hash != digest:
            raise IdempotencyConflict(idempotency_key)
        return existing

    payment = Payment(
        amount=amount,
        currency=data.currency.value,
        description=data.description,
        extra_metadata=data.metadata,
        idempotency_key=idempotency_key,
        request_hash=digest,
        webhook_url=str(data.webhook_url),
    )
    session.add(payment)

    try:
        await session.flush()
        session.add(
            Outbox(
                event_type="payments.new",
                aggregate_id=payment.id,
                payload=PaymentNewEvent(payment_id=payment.id).model_dump(mode="json"),
            )
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raced = await session.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
        if raced is None:
            raise
        if raced.request_hash != digest:
            raise IdempotencyConflict(idempotency_key)
        return raced

    await session.refresh(payment)
    logger.info("создан платёж %s", payment.id)
    return payment
