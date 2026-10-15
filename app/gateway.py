import asyncio
import logging
import random
import uuid

from app.config import settings
from app.models.enums import PaymentStatus

logger = logging.getLogger(__name__)


def decide_gateway_status(
    payment_id: uuid.UUID,
    success_rate: float = settings.gateway_success_rate,
) -> PaymentStatus:
    """Стабильный исход по payment_id: повторная доставка не меняет succeeded/failed."""
    rate = min(max(success_rate, 0.0), 1.0)
    sample = (payment_id.int % 10_000) / 10_000
    if sample < rate:
        return PaymentStatus.SUCCEEDED
    return PaymentStatus.FAILED


async def emulate_gateway(payment_id: uuid.UUID) -> tuple[PaymentStatus, str | None]:
    delay = random_delay()
    logger.info("эмуляция шлюза %s (%.2fс)", payment_id, delay)
    await asyncio.sleep(delay)
    status = decide_gateway_status(payment_id)
    if status is PaymentStatus.SUCCEEDED:
        return PaymentStatus.SUCCEEDED, None
    return PaymentStatus.FAILED, "шлюз отклонил платёж"


def random_delay() -> float:
    low = settings.gateway_delay_min
    high = settings.gateway_delay_max
    if high < low:
        low, high = high, low
    return random.uniform(low, high)
