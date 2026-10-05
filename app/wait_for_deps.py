import asyncio
import logging
import sys

import asyncpg
from aio_pika import connect_robust
from sqlalchemy.engine.url import make_url

from app.config import settings

logger = logging.getLogger(__name__)


async def _wait_postgres(tries: int = 30) -> None:
    url = make_url(settings.database_url)
    last: Exception | None = None
    for n in range(1, tries + 1):
        try:
            conn = await asyncpg.connect(
                host=url.host,
                port=url.port or 5432,
                user=url.username,
                password=url.password,
                database=url.database,
            )
            await conn.close()
            logger.info("postgres доступен")
            return
        except Exception as exc:
            last = exc
            logger.warning("ждём postgres %s/%s: %s", n, tries, exc)
            await asyncio.sleep(1)
    raise RuntimeError(f"postgres недоступен: {last}") from last


async def _wait_rabbit(tries: int = 30) -> None:
    last: Exception | None = None
    for n in range(1, tries + 1):
        try:
            conn = await connect_robust(settings.rabbitmq_url)
            await conn.close()
            logger.info("rabbitmq доступен")
            return
        except Exception as exc:
            last = exc
            logger.warning("ждём rabbitmq %s/%s: %s", n, tries, exc)
            await asyncio.sleep(1)
    raise RuntimeError(f"rabbitmq недоступен: {last}") from last


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await _wait_postgres()
    await _wait_rabbit()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
