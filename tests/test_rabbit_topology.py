from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aio_pika import ExchangeType as AioExchangeType

from app.rabbit import (
    DLX,
    EXCHANGE,
    QUEUE_DLQ,
    QUEUE_NEW,
    declare_topology,
    main_queue_arguments,
    retry_queue_arguments,
    retry_queue_name,
)
from app.retry import RETRY_TTL_MS


def test_main_queue_dead_letters_to_dlq() -> None:
    args = main_queue_arguments()
    assert args["x-dead-letter-exchange"] == DLX
    assert args["x-dead-letter-routing-key"] == QUEUE_DLQ


def test_retry_queues_expire_back_to_main() -> None:
    for ttl in RETRY_TTL_MS:
        args = retry_queue_arguments(ttl)
        assert args["x-message-ttl"] == ttl
        assert args["x-dead-letter-exchange"] == EXCHANGE
        assert args["x-dead-letter-routing-key"] == QUEUE_NEW


async def test_declare_topology_binds_dlq_and_retry_queues() -> None:
    declared_queues: list[tuple[str, dict | None]] = []
    binds: list[tuple[object, str]] = []

    class FakeQueue:
        async def bind(self, exchange, routing_key):
            binds.append((exchange, routing_key))

    channel = SimpleNamespace()
    channel.declare_exchange = AsyncMock(return_value="ex")
    channel.declare_queue = AsyncMock(
        side_effect=lambda name, durable=True, arguments=None: (
            declared_queues.append((name, arguments)),
            FakeQueue(),
        )[1]
    )

    connection = SimpleNamespace()
    connection.channel = AsyncMock(return_value=channel)
    connection.close = AsyncMock()

    with patch("app.rabbit.connect_robust", new_callable=AsyncMock, return_value=connection):
        await declare_topology("amqp://guest:guest@localhost/")

    connection.close.assert_awaited_once()
    channel.declare_exchange.assert_any_await(EXCHANGE, AioExchangeType.DIRECT, durable=True)
    channel.declare_exchange.assert_any_await(DLX, AioExchangeType.DIRECT, durable=True)

    names = [name for name, _ in declared_queues]
    assert QUEUE_NEW in names
    assert QUEUE_DLQ in names
    for ttl in RETRY_TTL_MS:
        assert retry_queue_name(ttl) in names

    main_args = next(args for name, args in declared_queues if name == QUEUE_NEW)
    assert main_args == main_queue_arguments()
    assert ("ex", QUEUE_DLQ) in binds
    assert ("ex", QUEUE_NEW) in binds
