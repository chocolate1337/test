import pytest

from app.rabbit import retry_queue_name
from app.retry import FailureAction, attempt_from_headers, route_failure


def test_first_delivery_is_attempt_one() -> None:
    assert attempt_from_headers(None) == 1
    assert attempt_from_headers({}) == 1


def test_header_increments_attempt() -> None:
    assert attempt_from_headers({"x-retry-count": 1}) == 2
    assert attempt_from_headers({"x-retry-count": "2"}) == 3


def test_broken_header_starts_from_one() -> None:
    assert attempt_from_headers({"x-retry-count": "nope"}) == 1


def test_attempts_one_and_two_go_to_retry_with_exponential_ttl() -> None:
    first = route_failure(1)
    second = route_failure(2)
    assert first.action is FailureAction.RETRY
    assert second.action is FailureAction.RETRY
    assert first.retry_ttl_ms == 1000
    assert second.retry_ttl_ms == 2000
    assert retry_queue_name(1000) == "payments.new.retry.1000ms"


def test_third_attempt_goes_to_dlq() -> None:
    route = route_failure(3)
    assert route.action is FailureAction.DEAD_LETTER
    assert route.retry_ttl_ms is None


def test_attempt_below_one_is_invalid() -> None:
    with pytest.raises(ValueError):
        route_failure(0)
