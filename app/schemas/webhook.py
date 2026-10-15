import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_serializer

from app.models.enums import Currency, PaymentStatus


class WebhookEvent(BaseModel):
    payment_id: uuid.UUID
    status: PaymentStatus
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any]
    failure_reason: str | None = None
    created_at: datetime
    processed_at: datetime | None = None

    @field_serializer("amount")
    def _amount(self, value: Decimal) -> str:
        return f"{value:.2f}"
