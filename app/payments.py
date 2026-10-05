import hashlib
import json
import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Outbox, Payment
from app.schemas import KOPECKS, PaymentCreate, PaymentNewEvent, PaymentOut

logger = logging.getLogger(__name__)


class IdempotencyConflict(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"ключ {key} уже занят другим запросом")
        self.key = key


def to_out(payment: Payment) -> PaymentOut:
    return PaymentOut.model_validate(payment)


def _fingerprint(
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
    result = await session.execute(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    return result.scalar_one_or_none()


async def create_payment(
    session: AsyncSession,
    data: PaymentCreate,
    idempotency_key: str,
) -> Payment:
    amount = data.amount.quantize(KOPECKS)
    digest = _fingerprint(
        amount=amount,
        currency=data.currency.value,
        description=data.description,
        metadata=data.metadata,
        webhook_url=str(data.webhook_url),
    )

    existing = await session.scalar(
        select(Payment).where(Payment.idempotency_key == idempotency_key)
    )
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
        raced = await session.scalar(
            select(Payment).where(Payment.idempotency_key == idempotency_key)
        )
        if raced is None:
            raise
        if raced.request_hash != digest:
            raise IdempotencyConflict(idempotency_key)
        return raced

    await session.refresh(payment)
    logger.info("создан платёж %s", payment.id)
    return payment
