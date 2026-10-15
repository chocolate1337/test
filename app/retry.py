from dataclasses import dataclass
from enum import Enum

RETRY_HEADER = "x-retry-count"
MAX_ATTEMPTS = 3
RETRY_TTL_MS = (1000, 2000)


class FailureAction(str, Enum):
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class FailureRoute:
    action: FailureAction
    retry_ttl_ms: int | None = None


def attempt_from_headers(headers: dict | None) -> int:
    raw = (headers or {}).get(RETRY_HEADER, 0)
    try:
        previous = int(raw)
    except (TypeError, ValueError):
        previous = 0
    return max(previous, 0) + 1


def route_failure(
    attempt: int,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    retry_ttls: tuple[int, ...] = RETRY_TTL_MS,
) -> FailureRoute:
    """Куда девать сообщение после неудачной попытки обработки.

    attempt — номер текущей попытки, начиная с 1.
    Попытки 1 и 2 уходят в TTL-retry (1s, затем 2s), третья — в DLQ.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    if attempt >= max_attempts:
        return FailureRoute(action=FailureAction.DEAD_LETTER)
    return FailureRoute(action=FailureAction.RETRY, retry_ttl_ms=retry_ttls[attempt - 1])
