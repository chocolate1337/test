import uuid

from sqlalchemy import func, select

from app.models.outbox import Outbox
from app.models.payment import Payment

API_KEY = "dev-api-key"
HEADERS = {"X-API-Key": API_KEY, "Idempotency-Key": "order-42"}
BODY = {
    "amount": "150.50",
    "description": "Оплата заказа #42",
    "metadata": {"order_id": "42"},
    "webhook_url": "https://example.test/hook",
}


async def test_health_requires_api_key(client) -> None:
    assert (await client.get("/health")).status_code == 401
    response = await client.get("/health", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_docs_and_openapi_require_api_key(client) -> None:
    assert (await client.get("/docs")).status_code == 401
    assert (await client.get("/openapi.json")).status_code == 401
    docs = await client.get("/docs", headers={"X-API-Key": API_KEY})
    spec = await client.get("/openapi.json", headers={"X-API-Key": API_KEY})
    assert docs.status_code == 200
    assert spec.status_code == 200
    assert spec.json()["security"] == [{"ApiKeyAuth": []}]


async def test_payments_require_api_key(client) -> None:
    response = await client.post("/api/v1/payments", json=BODY, headers={"Idempotency-Key": "x"})
    assert response.status_code == 401


async def test_create_and_get_payment(client) -> None:
    created = await client.post("/api/v1/payments", json=BODY, headers=HEADERS)
    assert created.status_code == 202
    payload = created.json()
    assert payload["status"] == "pending"
    payment_id = payload["payment_id"]

    fetched = await client.get(f"/api/v1/payments/{payment_id}", headers={"X-API-Key": API_KEY})
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["amount"] == "150.50"
    assert body["currency"] == "RUB"
    assert body["metadata"] == {"order_id": "42"}
    assert body["processed_at"] is None


async def test_create_is_idempotent(client) -> None:
    first = await client.post("/api/v1/payments", json=BODY, headers=HEADERS)
    second = await client.post("/api/v1/payments", json=BODY, headers=HEADERS)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["payment_id"] == second.json()["payment_id"]


async def test_idempotency_conflict(client) -> None:
    await client.post("/api/v1/payments", json=BODY, headers=HEADERS)
    other = {**BODY, "description": "другое"}
    response = await client.post("/api/v1/payments", json=other, headers=HEADERS)
    assert response.status_code == 409


async def test_missing_idempotency_key(client) -> None:
    response = await client.post("/api/v1/payments", json=BODY, headers={"X-API-Key": API_KEY})
    assert response.status_code == 422


async def test_get_unknown_payment(client) -> None:
    response = await client.get(f"/api/v1/payments/{uuid.uuid4()}", headers={"X-API-Key": API_KEY})
    assert response.status_code == 404


async def test_create_rejects_extra_decimal_places(client) -> None:
    response = await client.post(
        "/api/v1/payments",
        json={**BODY, "amount": "10.123"},
        headers=HEADERS,
    )
    assert response.status_code == 422


async def test_create_persists_single_outbox_row(client, session_factory) -> None:
    await client.post("/api/v1/payments", json=BODY, headers=HEADERS)
    async with session_factory() as session:
        payments = await session.scalar(select(func.count()).select_from(Payment))
        outbox = await session.scalar(select(func.count()).select_from(Outbox))
    assert payments == 1
    assert outbox == 1
