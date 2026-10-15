import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, field_serializer

from app.models.enums import Currency, PaymentStatus

KOPECKS = Decimal("0.01")


class PaymentCreate(BaseModel):
    amount: Decimal = Field(..., gt=0, max_digits=18, decimal_places=2)
    currency: Currency = Currency.RUB
    description: str = Field(..., min_length=1, max_length=1024)
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: HttpUrl


class PaymentAccepted(BaseModel):
    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any] = Field(
        validation_alias=AliasChoices("extra_metadata", "metadata"),
        serialization_alias="metadata",
    )
    status: PaymentStatus
    webhook_url: str
    failure_reason: str | None = None
    created_at: datetime
    processed_at: datetime | None = None

    @field_serializer("amount")
    def _amount(self, value: Decimal) -> str:
        return f"{value:.2f}"
