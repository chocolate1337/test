import asyncio
import logging

from faststream import FastStream
from faststream.rabbit import RabbitMessage

from app.config import settings
from app.process import PermanentError, WebhookInFlightError, process_payment
from app.rabbit import (
    consumer_channel,
    declare_topology,
    new_broker,
    new_queue,
    payments_exchange,
    retry_queue_name,
)
from app.retry import (
    MAX_ATTEMPTS,
    RETRY_HEADER,
    FailureAction,
    attempt_from_headers,
    route_failure,
)
from app.schemas.events import PaymentNewEvent

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

broker = new_broker()
app = FastStream(broker)


@app.on_startup
async def _topology() -> None:
    await declare_topology(settings.rabbitmq_url)


@broker.subscriber(new_queue, payments_exchange, no_ack=True, channel=consumer_channel)
async def on_payment_created(event: PaymentNewEvent, msg: RabbitMessage) -> None:
    attempt = attempt_from_headers(msg.headers)
    logger.info("платёж %s, попытка %s/%s", event.payment_id, attempt, MAX_ATTEMPTS)
    try:
        await process_payment(event.payment_id)
    except Exception as exc:
        logger.exception("ошибка обработки %s, попытка %s", event.payment_id, attempt)
        await settle_failure(event, msg, attempt, exc)
        return
    await msg.ack()


async def settle_failure(event: PaymentNewEvent, msg: RabbitMessage, attempt: int, exc: BaseException) -> None:
    if isinstance(exc, PermanentError):
        await msg.reject(requeue=False)
        logger.error("%s в DLQ без ретрая (%s)", event.payment_id, type(exc).__name__)
        return
    if isinstance(exc, WebhookInFlightError):
        await asyncio.sleep(settings.webhook_inflight_nack_delay_seconds)
        await msg.nack(requeue=True)
        logger.info("%s in-flight, nack+requeue", event.payment_id)
        return
    await retry_or_dead_letter(event, msg, attempt)


async def retry_or_dead_letter(event: PaymentNewEvent, msg: RabbitMessage, attempt: int) -> None:
    route = route_failure(attempt)
    if route.action is FailureAction.DEAD_LETTER:
        await msg.reject(requeue=False)
        logger.error("%s ушёл в DLQ через reject(requeue=False)", event.payment_id)
        return

    assert route.retry_ttl_ms is not None
    try:
        await broker.publish(
            event.model_dump(mode="json"),
            queue=retry_queue_name(route.retry_ttl_ms),
            persist=True,
            headers={**(msg.headers or {}), RETRY_HEADER: attempt},
            message_id=str(event.payment_id),
            correlation_id=str(event.payment_id),
        )
    except Exception:
        logger.exception("не удалось опубликовать retry для %s, nack+requeue", event.payment_id)
        await msg.nack(requeue=True)
        raise

    await msg.ack()
    logger.warning("%s повторим через %s мс", event.payment_id, route.retry_ttl_ms)


if __name__ == "__main__":
    asyncio.run(app.run())
