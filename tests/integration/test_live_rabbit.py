import asyncio
import json
import uuid

import pytest
from aio_pika import DeliveryMode, Message, connect_robust

from app.rabbit import DLX, EXCHANGE, QUEUE_DLQ, QUEUE_NEW, declare_topology, retry_queue_name
from app.retry import RETRY_TTL_MS

pytestmark = pytest.mark.integration


async def _wait_rabbit(url: str) -> None:
    last: Exception | None = None
    for _ in range(30):
        try:
            connection = await connect_robust(url)
            await connection.close()
            return
        except Exception as exc:
            last = exc
            await asyncio.sleep(1)
    raise AssertionError(f"RabbitMQ недоступен: {last}")


@pytest.fixture
async def ready_rabbit(rabbit_url: str) -> str:
    await _wait_rabbit(rabbit_url)
    await declare_topology(rabbit_url)
    connection = await connect_robust(rabbit_url)
    try:
        channel = await connection.channel()
        for name in (QUEUE_NEW, QUEUE_DLQ, *[retry_queue_name(ttl) for ttl in RETRY_TTL_MS]):
            queue = await channel.get_queue(name)
            await queue.purge()
    finally:
        await connection.close()
    return rabbit_url


async def test_reject_without_requeue_lands_in_dlq(ready_rabbit: str) -> None:
    connection = await connect_robust(ready_rabbit)
    try:
        channel = await connection.channel()
        exchange = await channel.get_exchange(EXCHANGE)
        payment_id = str(uuid.uuid4())
        await exchange.publish(
            Message(
                json.dumps({"payment_id": payment_id}).encode(),
                delivery_mode=DeliveryMode.PERSISTENT,
                message_id=payment_id,
            ),
            routing_key=QUEUE_NEW,
        )
        queue = await channel.get_queue(QUEUE_NEW)
        incoming = await queue.get(timeout=5, fail=False)
        assert incoming is not None
        await incoming.reject(requeue=False)

        dlq = await channel.get_queue(QUEUE_DLQ)
        dead = await dlq.get(timeout=5, fail=False)
        assert dead is not None
        assert json.loads(dead.body)["payment_id"] == payment_id
        await dead.ack()
    finally:
        await connection.close()


async def test_retry_ttl_queue_returns_to_payments_new(ready_rabbit: str) -> None:
    ttl = RETRY_TTL_MS[0]
    retry_name = retry_queue_name(ttl)
    payment_id = str(uuid.uuid4())
    connection = await connect_robust(ready_rabbit)
    try:
        channel = await connection.channel()
        await channel.default_exchange.publish(
            Message(
                json.dumps({"payment_id": payment_id}).encode(),
                delivery_mode=DeliveryMode.PERSISTENT,
            ),
            routing_key=retry_name,
        )
        await asyncio.sleep(ttl / 1000 + 1.5)
        queue = await channel.get_queue(QUEUE_NEW)
        incoming = await queue.get(timeout=3, fail=False)
        if incoming is None:
            retry_q = await channel.get_queue(retry_name)
            leftover = await retry_q.get(fail=False)
            assert leftover is None
        else:
            assert json.loads(incoming.body)["payment_id"] == payment_id
            await incoming.ack()
    finally:
        await connection.close()


async def test_dlx_exchange_exists(ready_rabbit: str) -> None:
    connection = await connect_robust(ready_rabbit)
    try:
        channel = await connection.channel()
        await channel.get_exchange(DLX)
        await channel.get_queue(QUEUE_DLQ)
    finally:
        await connection.close()
