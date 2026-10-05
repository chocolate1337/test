import asyncio
import logging
import random
import uuid
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.db import SessionLocal
from app.models import PaymentStatus
from app.payments import get_payment, lock_payment
from app.schemas import WebhookEvent

logger = logging.getLogger(__name__)


class WebhookError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(f"не удалось отправить webhook: {reason}")


async def emulate_gateway(payment_id: uuid.UUID) -> tuple[PaymentStatus, str | None]:
    delay = random.uniform(settings.gateway_delay_min, settings.gateway_delay_max)
    logger.info("эмуляция шлюза %s (%.2fс)", payment_id, delay)
    await asyncio.sleep(delay)
    if random.random() < settings.gateway_success_rate:
        return PaymentStatus.SUCCEEDED, None
        return PaymentStatus.FAILED, "шлюз отклонил платёж"


async def deliver_webhook(url: str, event: WebhookEvent) -> None:
    last_error: Exception | None = None
    body = event.model_dump(mode="json")

    async with httpx.AsyncClient(timeout=settings.webhook_timeout) as client:
        for attempt in range(1, settings.webhook_attempts + 1):
            try:
                response = await client.post(url, json=body)
                response.raise_for_status()
                logger.info("webhook ушёл на %s, попытка %s", url, attempt)
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "webhook не дошёл до %s, попытка %s/%s: %s",
                    url,
                    attempt,
                    settings.webhook_attempts,
                    exc,
                )
                if attempt < settings.webhook_attempts:
                    await asyncio.sleep(2 ** (attempt - 1))

    raise WebhookError(str(last_error)) from last_error


async def process_payment(payment_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        payment = await get_payment(session, payment_id)

    if payment is None:
        logger.error("платёж %s не найден, дропаем сообщение", payment_id)
        return

    if payment.status == PaymentStatus.PENDING.value:
        status, reason = await emulate_gateway(payment.id)
        async with SessionLocal() as session:
            async with session.begin():
                locked = await lock_payment(session, payment_id)
                if locked is None:
                    logger.error("платёж %s пропал пока ждали шлюз", payment_id)
                    return
                if locked.status == PaymentStatus.PENDING.value:
                    locked.status = status.value
                    locked.failure_reason = reason
                    locked.processed_at = datetime.now(timezone.utc)
                payment = locked

    if payment.webhook_sent_at is not None:
        return

    await deliver_webhook(
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

    async with SessionLocal() as session:
        async with session.begin():
            current = await get_payment(session, payment_id)
            if current is not None and current.webhook_sent_at is None:
                current.webhook_sent_at = datetime.now(timezone.utc)
