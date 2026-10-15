import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_api_key
from app.db import get_db
from app.payments import IdempotencyConflict, create_payment, get_payment, to_out
from app.schemas.payment import PaymentAccepted, PaymentCreate, PaymentOut

router = APIRouter(
    prefix="/api/v1/payments",
    tags=["payments"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=PaymentAccepted)
async def create_payment_view(
    body: PaymentCreate,
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> PaymentAccepted:
    try:
        payment = await create_payment(session, body, idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return PaymentAccepted(
        payment_id=payment.id,
        status=payment.status,
        created_at=payment.created_at,
    )


@router.get("/{payment_id}", response_model=PaymentOut)
async def get_payment_view(
    payment_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PaymentOut:
    payment = await get_payment(session, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Платёж не найден")
    return to_out(payment)
