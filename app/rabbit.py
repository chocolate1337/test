"""Rabbit topology.

Retry без delayed-plugin: TTL-очереди возвращают сообщение в payments.new.
После 3 попыток кладём в DLQ руками очередь не читаем, её смотрят в UI.
"""

from aio_pika import ExchangeType as AioExchangeType
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue

from app.config import settings

EXCHANGE = "payments"
DLX = "payments.dlx"
QUEUE_NEW = "payments.new"
QUEUE_DLQ = "payments.new.dlq"

# попытка 2 через 1s, попытка 3 через 2s
RETRY_TTL_MS = (1000, 2000)

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


def new_broker() -> RabbitBroker:
    return RabbitBroker(settings.rabbitmq_url)


def retry_queue_name(ttl_ms: int) -> str:
    return f"payments.new.retry.{ttl_ms}ms"


async def declare_topology(broker: RabbitBroker) -> None:
    channel = getattr(broker, "_channel", None)
    owned = False
    if channel is None:
        connection = getattr(broker, "_connection", None)
        if connection is None:
            raise RuntimeError("нет соединения с RabbitMQ")
        channel = await connection.channel()
        owned = True

    try:
        payments = await channel.declare_exchange(EXCHANGE, AioExchangeType.DIRECT, durable=True)
        dlx = await channel.declare_exchange(DLX, AioExchangeType.DIRECT, durable=True)

        incoming = await channel.declare_queue(
            QUEUE_NEW,
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX,
                "x-dead-letter-routing-key": QUEUE_DLQ,
            },
        )
        await incoming.bind(payments, routing_key=QUEUE_NEW)

        dead = await channel.declare_queue(QUEUE_DLQ, durable=True)
        await dead.bind(dlx, routing_key=QUEUE_DLQ)

        for ttl in RETRY_TTL_MS:
            await channel.declare_queue(
                retry_queue_name(ttl),
                durable=True,
                arguments={
                    "x-message-ttl": ttl,
                    "x-dead-letter-exchange": EXCHANGE,
                    "x-dead-letter-routing-key": QUEUE_NEW,
                },
            )
    finally:
        if owned:
            await channel.close()
