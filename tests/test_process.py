import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.models.enums import PaymentStatus
from app.payments import get_payment, try_claim_webhook
from app.process import PaymentNotFoundError, WebhookInFlightError, process_payment
from tests.conftest import make_payment


async def test_unknown_payment_is_permanent_error(session_factory) -> None:
    charge = AsyncMock()
    notify = AsyncMock()
    with pytest.raises(PaymentNotFoundError):
        await process_payment(uuid.uuid4(), session_factory=session_factory, charge=charge, notify=notify)
    charge.assert_not_awaited()
    notify.assert_not_awaited()


async def test_crash_after_gateway_returns_before_persist_keeps_pending(session, session_factory) -> None:
    payment = make_payment()
    session.add(payment)
    await session.commit()

    async def charge(_payment_id):
        return PaymentStatus.SUCCEEDED, None

    calls = {"n": 0}
    real = session_factory

    def factory():
        calls["n"] += 1
        if calls["n"] == 2:

            class Boom:
                async def __aenter__(self):
                    raise RuntimeError("db недоступна после ответа шлюза")

                async def __aexit__(self, *args):
                    return False

            return Boom()
        return real()

    with pytest.raises(RuntimeError, match="db недоступна"):
        await process_payment(payment.id, session_factory=factory, charge=charge, notify=AsyncMock())

    async with session_factory() as db:
        stored = await get_payment(db, payment.id)
    assert stored is not None
    assert stored.status == PaymentStatus.PENDING.value
    assert stored.processed_at is None
    assert stored.webhook_sent_at is None


async def test_retry_after_persist_skips_gateway_and_sends_webhook(session, session_factory) -> None:
    payment = make_payment(status=PaymentStatus.SUCCEEDED.value, processed_at=datetime.now(timezone.utc))
    session.add(payment)
    await session.commit()

    charge = AsyncMock()
    notify = AsyncMock()
    await process_payment(payment.id, session_factory=session_factory, charge=charge, notify=notify)
    charge.assert_not_awaited()
    notify.assert_awaited_once()

    async with session_factory() as db:
        stored = await get_payment(db, payment.id)
    assert stored is not None
    assert stored.webhook_sent_at is not None


async def test_already_notified_payment_does_not_resend_or_touch_timestamp(
    session, session_factory
) -> None:
    sent_at = datetime.now(timezone.utc)
    payment = make_payment(
        status=PaymentStatus.SUCCEEDED.value,
        processed_at=sent_at,
        webhook_sent_at=sent_at,
    )
    session.add(payment)
    await session.commit()

    charge = AsyncMock()
    notify = AsyncMock()
    await process_payment(payment.id, session_factory=session_factory, charge=charge, notify=notify)
    charge.assert_not_awaited()
    notify.assert_not_awaited()

    async with session_factory() as db:
        stored = await get_payment(db, payment.id)
    assert stored is not None
    assert stored.webhook_sent_at.replace(tzinfo=timezone.utc) == sent_at.replace(tzinfo=timezone.utc)


async def test_webhook_failure_keeps_terminal_status_and_allows_retry(session, session_factory) -> None:
    payment = make_payment()
    session.add(payment)
    await session.commit()

    async def charge(_payment_id):
        return PaymentStatus.SUCCEEDED, None

    notify = AsyncMock(side_effect=RuntimeError("webhook 500"))
    try:
        await process_payment(payment.id, session_factory=session_factory, charge=charge, notify=notify)
    except RuntimeError:
        pass

    async with session_factory() as db:
        stored = await get_payment(db, payment.id)
    assert stored is not None
    assert stored.status == PaymentStatus.SUCCEEDED.value
    assert stored.processed_at is not None
    assert stored.webhook_sent_at is None

    notify_ok = AsyncMock()
    await process_payment(
        payment.id,
        session_factory=session_factory,
        charge=AsyncMock(),
        notify=notify_ok,
    )
    notify_ok.assert_awaited_once()
    async with session_factory() as db:
        stored = await get_payment(db, payment.id)
    assert stored is not None
    assert stored.webhook_sent_at is not None


async def test_happy_path_charges_then_notifies(session, session_factory) -> None:
    payment = make_payment()
    session.add(payment)
    await session.commit()

    async def charge(_payment_id):
        return PaymentStatus.FAILED, "шлюз отклонил платёж"

    notify = AsyncMock()
    await process_payment(payment.id, session_factory=session_factory, charge=charge, notify=notify)
    notify.assert_awaited_once()
    event = notify.await_args.args[1]
    assert event.status is PaymentStatus.FAILED
    assert event.failure_reason == "шлюз отклонил платёж"

    async with session_factory() as db:
        stored = await get_payment(db, payment.id)
    assert stored is not None
    assert stored.status == PaymentStatus.FAILED.value
    assert stored.webhook_sent_at is not None


async def test_second_worker_does_not_send_webhook_while_claimed(session, session_factory) -> None:
    payment = make_payment(status=PaymentStatus.SUCCEEDED.value, processed_at=datetime.now(timezone.utc))
    session.add(payment)
    await session.commit()

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_notify(*_args, **_kwargs):
        started.set()
        await release.wait()

    first = asyncio.create_task(
        process_payment(
            payment.id,
            session_factory=session_factory,
            charge=AsyncMock(),
            notify=slow_notify,
        )
    )
    await started.wait()
    notify = AsyncMock()
    with pytest.raises(WebhookInFlightError):
        await process_payment(
            payment.id,
            session_factory=session_factory,
            charge=AsyncMock(),
            notify=notify,
        )
    notify.assert_not_awaited()
    release.set()
    await first


async def test_stale_claim_can_be_taken_after_crash(session, session_factory) -> None:
    from datetime import timedelta

    from app.config import settings

    payment = make_payment(status=PaymentStatus.SUCCEEDED.value, processed_at=datetime.now(timezone.utc))
    session.add(payment)
    await session.commit()
    assert await try_claim_webhook(session, payment.id)
    await session.commit()
    payment.webhook_claimed_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.webhook_claim_ttl_seconds + 1
    )
    await session.commit()

    notify = AsyncMock()
    await process_payment(
        payment.id,
        session_factory=session_factory,
        charge=AsyncMock(),
        notify=notify,
    )
    notify.assert_awaited_once()
