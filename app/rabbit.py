"""Топология RabbitMQ объявляется через aio-pika, без приватного API FastStream.

payments (direct, durable)
  └─ payments.new  (DLX → payments.dlx / payments.new.dlq)

Retry без delayed-plugin: TTL-очереди возвращают сообщение в payments.new.
После 3-й ошибки consumer делает reject(requeue=False) — брокер кладёт
исходное сообщение в DLQ. Prefetch=1: один unacked message на consumer.
"""

from aio_pika import ExchangeType as AioExchangeType
from aio_pika import connect_robust
from faststream.rabbit import Channel, ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue

from app.config import settings
from app.retry import RETRY_TTL_MS

EXCHANGE = "payments"
DLX = "payments.dlx"
QUEUE_NEW = "payments.new"
QUEUE_DLQ = "payments.new.dlq"

consumer_channel = Channel(prefetch_count=1, publisher_confirms=True)

payments_exchange = RabbitExchange(EXCHANGE, type=ExchangeType.DIRECT, durable=True)
dlx_exchange = RabbitExchange(DLX, type=ExchangeType.DIRECT, durable=True)

new_queue = RabbitQueue(
    QUEUE_NEW,
    durable=True,
    routing_key=QUEUE_NEW,
    arguments={
        "x-dead-letter-exchange": DLX,
        "x-dead-letter-routing-key": QUEUE_DLQ,
    },
)


def new_broker(url: str | None = None) -> RabbitBroker:
    return RabbitBroker(url or settings.rabbitmq_url, default_channel=consumer_channel)


def retry_queue_name(ttl_ms: int) -> str:
    return f"payments.new.retry.{ttl_ms}ms"


def retry_queue_arguments(ttl_ms: int) -> dict:
    return {
        "x-message-ttl": ttl_ms,
        "x-dead-letter-exchange": EXCHANGE,
        "x-dead-letter-routing-key": QUEUE_NEW,
    }


def main_queue_arguments() -> dict:
    return {
        "x-dead-letter-exchange": DLX,
        "x-dead-letter-routing-key": QUEUE_DLQ,
    }


async def declare_topology(url: str | None = None) -> None:
    connection = await connect_robust(url or settings.rabbitmq_url)
    try:
        channel = await connection.channel()
        payments = await channel.declare_exchange(EXCHANGE, AioExchangeType.DIRECT, durable=True)
        dlx = await channel.declare_exchange(DLX, AioExchangeType.DIRECT, durable=True)

        incoming = await channel.declare_queue(
            QUEUE_NEW,
            durable=True,
            arguments=main_queue_arguments(),
        )
        await incoming.bind(payments, routing_key=QUEUE_NEW)

        dead = await channel.declare_queue(QUEUE_DLQ, durable=True)
        await dead.bind(dlx, routing_key=QUEUE_DLQ)

        for ttl in RETRY_TTL_MS:
            await channel.declare_queue(
                retry_queue_name(ttl),
                durable=True,
                arguments=retry_queue_arguments(ttl),
            )
    finally:
        await connection.close()
