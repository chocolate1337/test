import asyncio
import logging

from faststream import FastStream
from faststream.rabbit import RabbitMessage

from app.config import settings
from app.process import process_payment
from app.rabbit import (
    QUEUE_DLQ,
    RETRY_TTL_MS,
    declare_topology,
    dlx_exchange,
    new_broker,
    new_queue,
    payments_exchange,
    retry_queue_name,
)
from app.schemas import PaymentNewEvent

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_HEADER = "x-retry-count"

broker = new_broker()
app = FastStream(broker)


@app.on_startup
async def _topology() -> None:
    await broker.connect()
    await declare_topology(broker)


@broker.subscriber(new_queue, payments_exchange)
async def on_payment_created(event: PaymentNewEvent, msg: RabbitMessage) -> None:
    attempt = _attempt_no(msg)
    logger.info("платёж %s, попытка %s/%s", event.payment_id, attempt, MAX_ATTEMPTS)
    try:
        await process_payment(event.payment_id)
    except Exception:
        logger.exception("ошибка обработки %s, попытка %s", event.payment_id, attempt)
        await _retry_or_dlq(event, msg, attempt)


def _attempt_no(msg: RabbitMessage) -> int:
    raw = (msg.headers or {}).get(RETRY_HEADER, 0)
    try:
        previous = int(raw)
    except (TypeError, ValueError):
        previous = 0
    return max(previous, 0) + 1


async def _retry_or_dlq(event: PaymentNewEvent, msg: RabbitMessage, attempt: int) -> None:
    headers = {**(msg.headers or {}), RETRY_HEADER: attempt}
    body = event.model_dump(mode="json")

    if attempt >= MAX_ATTEMPTS:
        await broker.publish(
            body,
            exchange=dlx_exchange,
            routing_key=QUEUE_DLQ,
            persist=True,
            headers=headers,
            message_id=str(event.payment_id),
            correlation_id=str(event.payment_id),
        )
        logger.error("%s ушёл в DLQ", event.payment_id)
        return

    ttl = RETRY_TTL_MS[attempt - 1]
    await broker.publish(
        body,
        queue=retry_queue_name(ttl),
        persist=True,
        headers=headers,
        message_id=str(event.payment_id),
        correlation_id=str(event.payment_id),
    )
    logger.warning("%s повторим через %s мс", event.payment_id, ttl)


if __name__ == "__main__":
    asyncio.run(app.run())
