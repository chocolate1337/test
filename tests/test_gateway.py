import uuid

from app.gateway import decide_gateway_status
from app.models.enums import PaymentStatus


def test_gateway_is_stable_for_same_payment() -> None:
    payment_id = uuid.uuid4()
    first = decide_gateway_status(payment_id)
    second = decide_gateway_status(payment_id)
    assert first is second


def test_gateway_can_succeed_and_fail() -> None:
    statuses = {decide_gateway_status(uuid.UUID(int=i), success_rate=0.9) for i in (0, 8999, 9000, 9999)}
    assert PaymentStatus.SUCCEEDED in statuses
    assert PaymentStatus.FAILED in statuses


def test_zero_success_rate_always_fails() -> None:
    assert decide_gateway_status(uuid.uuid4(), success_rate=0) is PaymentStatus.FAILED


def test_full_success_rate_always_succeeds() -> None:
    assert decide_gateway_status(uuid.uuid4(), success_rate=1) is PaymentStatus.SUCCEEDED
