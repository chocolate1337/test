import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.openapi.utils import get_openapi
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import router
from app.auth import APIKeyMiddleware, require_api_key
from app.config import settings
from app.db import get_db
from app.outbox import outbox_loop
from app.rabbit import declare_topology, new_broker

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


def create_app(*, start_workers: bool = True) -> FastAPI:
    broker = new_broker() if start_workers else None
    relay: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal relay
        if start_workers:
            assert broker is not None
            await broker.start()
            await declare_topology(settings.rabbitmq_url)
            relay = asyncio.create_task(outbox_loop(broker))
        yield
        if relay is not None:
            relay.cancel()
            try:
                await relay
            except asyncio.CancelledError:
                pass
        if broker is not None:
            await broker.stop()

    application = FastAPI(
        title="Payments",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.add_middleware(APIKeyMiddleware)
    application.include_router(router)

    def custom_openapi() -> dict:
        if application.openapi_schema:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            routes=application.routes,
        )
        schema.setdefault("components", {})["securitySchemes"] = {
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        }
        schema["security"] = [{"ApiKeyAuth": []}]
        application.openapi_schema = schema
        return schema

    application.openapi = custom_openapi  # type: ignore[method-assign]

    @application.get("/health")
    async def health(
        session: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[None, Depends(require_api_key)],
    ) -> dict[str, str]:
        await session.execute(text("SELECT 1"))
        return {"status": "ok"}

    return application


app = create_app()
