import asyncio
import json
import socket
import uuid

import pytest
from aio_pika import connect_robust
from fastapi import FastAPI, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from uvicorn import Config, Server

from app.config import settings
from app.models.base import Base
from app.models.enums import PaymentStatus
from app.outbox import publish_one
from app.payments import create_payment, get_payment
from app.process import process_payment
from app.rabbit import QUEUE_NEW, declare_topology, new_broker
from app.schemas.payment import PaymentCreate

pytestmark = pytest.mark.integration


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
async def pg_factory(postgres_url: str):
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS webhook_claimed_at TIMESTAMPTZ")
        )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


async def test_full_flow_outbox_process_webhook(pg_factory, rabbit_url: str, monkeypatch) -> None:
    monkeypatch.setattr(settings, "gateway_delay_min", 0.01)
    monkeypatch.setattr(settings, "gateway_delay_max", 0.02)

    hits: list[dict] = []
    hook_app = FastAPI()

    @hook_app.post("/hook")
    async def hook(request: Request) -> dict[str, str]:
        hits.append(await request.json())
        return {"ok": "true"}

    port = _free_port()
    server = Server(Config(hook_app, host="127.0.0.1", port=port, log_level="error"))
    server_task = asyncio.create_task(server.serve())
    for _ in range(50):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started

    await declare_topology(rabbit_url)
    broker = new_broker(rabbit_url)
    await broker.start()
    try:
        async with pg_factory() as session:
            payment = await create_payment(
                session,
                PaymentCreate(
                    amount="12.00",
                    description="e2e",
                    webhook_url=f"http://127.0.0.1:{port}/hook",
                ),
                f"e2e-{uuid.uuid4()}",
            )
            payment_id = payment.id
            assert await publish_one(session, broker)

        connection = await connect_robust(rabbit_url)
        try:
            channel = await connection.channel()
            queue = await channel.get_queue(QUEUE_NEW)
            incoming = await queue.get(timeout=3, fail=False)
            if incoming is not None:
                assert json.loads(incoming.body)["payment_id"] == str(payment_id)
                await incoming.ack()
        finally:
            await connection.close()

        await process_payment(payment_id, session_factory=pg_factory)

        async with pg_factory() as session:
            stored = await get_payment(session, payment_id)
        assert stored is not None
        assert stored.status in {PaymentStatus.SUCCEEDED.value, PaymentStatus.FAILED.value}
        assert stored.processed_at is not None
        assert stored.webhook_sent_at is not None
        assert len(hits) == 1
        assert hits[0]["payment_id"] == str(payment_id)
        assert hits[0]["status"] == stored.status
    finally:
        await broker.stop()
        server.should_exit = True
        await server_task
