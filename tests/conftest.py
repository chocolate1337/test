import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import create_app
from app.models.base import Base
from app.models.enums import PaymentStatus
from app.models.payment import Payment


@pytest.fixture
async def engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
async def client(session_factory) -> AsyncIterator[AsyncClient]:
    app = create_app(start_workers=False)

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def make_payment(**overrides) -> Payment:
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid.uuid4(),
        "amount": Decimal("150.50"),
        "currency": "RUB",
        "description": "заказ",
        "extra_metadata": {"order_id": "42"},
        "status": PaymentStatus.PENDING.value,
        "idempotency_key": str(uuid.uuid4()),
        "request_hash": "a" * 64,
        "webhook_url": "https://example.test/hook",
        "created_at": now,
    }
    values.update(overrides)
    return Payment(**values)
