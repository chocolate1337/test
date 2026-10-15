import asyncio
import logging

import httpx

from app.config import settings
from app.schemas.webhook import WebhookEvent

logger = logging.getLogger(__name__)


class WebhookError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(f"не удалось отправить webhook: {reason}")


def webhook_backoff_seconds(attempt: int) -> float:
    """Задержка после неудачной попытки attempt (1-based), перед следующей."""
    return float(2 ** (attempt - 1))


async def deliver_webhook(url: str, event: WebhookEvent) -> None:
    last_error: Exception | None = None
    body = event.model_dump(mode="json")

    async with httpx.AsyncClient(timeout=settings.webhook_timeout) as client:
        for attempt in range(1, settings.webhook_attempts + 1):
            try:
                response = await client.post(url, json=body)
                response.raise_for_status()
                logger.info("webhook ушёл на %s, попытка %s", url, attempt)
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "webhook не дошёл до %s, попытка %s/%s: %s",
                    url,
                    attempt,
                    settings.webhook_attempts,
                    exc,
                )
                if attempt < settings.webhook_attempts:
                    await asyncio.sleep(webhook_backoff_seconds(attempt))

    raise WebhookError(str(last_error)) from last_error
