import logging
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import SessionLocal
from app.gateway import emulate_gateway
from app.models.enums import PaymentStatus
from app.payments import (
    apply_gateway_result,
    get_payment,
    release_webhook_claim,
    try_claim_webhook,
    try_mark_webhook_sent,
)
from app.schemas.webhook import WebhookEvent
from app.webhook import deliver_webhook

logger = logging.getLogger(__name__)


class PermanentError(Exception):
    """Нельзя лечить ретраем — сообщение в DLQ."""


class PaymentNotFoundError(PermanentError):
    pass


class WebhookInFlightError(Exception):
    """Другой воркер держит claim, webhook ещё не подтверждён."""


async def process_payment(
    payment_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker = SessionLocal,
    charge=emulate_gateway,
    notify=deliver_webhook,
) -> None:
    async with session_factory() as session:
        payment = await get_payment(session, payment_id)

    if payment is None:
        raise PaymentNotFoundError(str(payment_id))

    if payment.status == PaymentStatus.PENDING.value:
        status, reason = await charge(payment.id)
        async with session_factory() as session:
            async with session.begin():
                payment = await apply_gateway_result(session, payment_id, status, reason)
        if payment is None:
            raise PaymentNotFoundError(str(payment_id))

    if payment.webhook_sent_at is not None:
        logger.info("webhook для %s уже отправлен, пропускаем", payment_id)
        return

    async with session_factory() as session:
        async with session.begin():
            claimed = await try_claim_webhook(session, payment_id)
    if not claimed:
        async with session_factory() as session:
            current = await get_payment(session, payment_id)
        if current is not None and current.webhook_sent_at is not None:
            return
        raise WebhookInFlightError(f"webhook для {payment_id} уже в полёте")

    try:
        await notify(
            payment.webhook_url,
            WebhookEvent(
                payment_id=payment.id,
                status=PaymentStatus(payment.status),
                amount=payment.amount,
                currency=payment.currency,
                description=payment.description,
                metadata=payment.extra_metadata,
                failure_reason=payment.failure_reason,
                created_at=payment.created_at,
                processed_at=payment.processed_at,
            ),
        )
    except Exception:
        async with session_factory() as session:
            async with session.begin():
                await release_webhook_claim(session, payment_id)
        raise

    async with session_factory() as session:
        async with session.begin():
            marked = await try_mark_webhook_sent(session, payment_id)
            if not marked:
                logger.info("webhook_sent_at для %s уже установлен другим воркером", payment_id)
