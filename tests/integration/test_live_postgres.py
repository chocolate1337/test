import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.outbox import publish_one
from app.payments import create_payment
from app.schemas.payment import PaymentCreate

pytestmark = pytest.mark.integration


@pytest.fixture
async def pg_session(postgres_url: str):
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS webhook_claimed_at TIMESTAMPTZ")
        )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_outbox_skip_locked_publish_on_postgres(pg_session) -> None:
    payment = await create_payment(
        pg_session,
        PaymentCreate(amount="10.00", description="pg", webhook_url="https://example.test/hook"),
        f"pg-key-{uuid.uuid4()}",
    )
    broker = AsyncMock()
    assert await publish_one(pg_session, broker)
    broker.publish.assert_awaited_once()
    assert broker.publish.await_args.kwargs["correlation_id"] == str(payment.id)
    assert await publish_one(pg_session, broker) is False
