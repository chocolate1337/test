import asyncio
import logging
from datetime import datetime, timezone

from faststream.rabbit import RabbitBroker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models.outbox import Outbox
from app.rabbit import QUEUE_NEW, payments_exchange

logger = logging.getLogger(__name__)


async def publish_one(session: AsyncSession, broker: RabbitBroker) -> bool:
    stmt = (
        select(Outbox)
        .where(Outbox.published_at.is_(None))
        .order_by(Outbox.created_at)
        .limit(1)
    )
    bind = session.bind
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    else:
        stmt = stmt.with_for_update()
    row = await session.scalar(stmt)
    if row is None:
        await session.rollback()
        return False

    await broker.publish(
        row.payload,
        exchange=payments_exchange,
        routing_key=QUEUE_NEW,
        persist=True,
        message_id=str(row.id),
        correlation_id=str(row.aggregate_id),
    )
    row.published_at = datetime.now(timezone.utc)
    await session.commit()
    logger.info("outbox отправил %s (%s)", row.id, row.event_type)
    return True


async def outbox_loop(broker: RabbitBroker) -> None:
    logger.info("outbox relay запущен")
    while True:
        try:
            published = 0
            async with SessionLocal() as session:
                while published < settings.outbox_batch_size and await publish_one(session, broker):
                    published += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("outbox relay упал")
        await asyncio.sleep(settings.outbox_poll_interval)
