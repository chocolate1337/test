import uuid

from pydantic import BaseModel


class PaymentNewEvent(BaseModel):
    payment_id: uuid.UUID
