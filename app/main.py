import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import router
from app.auth import require_api_key
from app.config import settings
from app.db import get_db
from app.outbox import outbox_loop
from app.rabbit import declare_topology, new_broker

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

broker = new_broker()
_relay: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _relay
    await broker.start()
    await declare_topology(broker)
    _relay = asyncio.create_task(outbox_loop(broker))
    yield
    if _relay is not None:
        _relay.cancel()
        try:
            await _relay
        except asyncio.CancelledError:
            pass
    await broker.stop()


app = FastAPI(
    title="Payments",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(require_api_key)],
)
app.include_router(router)


@app.get("/health")
async def health(session: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
