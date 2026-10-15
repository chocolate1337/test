# Payments

Асинхронный сервис оплаты: HTTP API принимает платёж, в одной транзакции пишет `payments` + `outbox`, relay публикует в RabbitMQ, один consumer эмулирует эквайринг и вызывает webhook.

## Запуск

```bash
cp .env.example .env
docker compose up --build
```

| Сервис | URL |
|--------|-----|
| API / OpenAPI | http://localhost:8000/docs |
| RabbitMQ UI | http://localhost:15673 (guest / guest) |
| Postgres с хоста | localhost:5433 |

Порты с хоста: `5433`, `5673`, `15673` — чтобы не пересекаться с локальными инстансами. Внутри сети compose — `postgres:5432` и `rabbitmq:5672`.

`docker compose down` останавливает стек. Volume с базой сохраняется.

## Тесты

```bash
python -m venv .venv
.venv/Scripts/activate   # Windows
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

- `pytest -q` — unit на SQLite; integration пропускаются, если нет `DATABASE_URL` / `RABBITMQ_URL`
- живые Postgres + RabbitMQ:

```bash
docker compose --profile test run --rm test
```

CI: `.github/workflows/test.yml` гоняет unit и integration на каждый push.

## API

`X-API-Key` обязателен на **всех** HTTP-запросах, включая `/health`, `/docs` и `/openapi.json`. Создание платежа — ещё `Idempotency-Key`: тот же ключ и тело → тот же платёж (`202`), другое тело → `409`.

```bash
curl -s -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-api-key" \
  -H "Idempotency-Key: order-42" \
  -d '{
    "amount": "150.50",
    "description": "Оплата заказа #42",
    "metadata": {"order_id": "42"},
    "webhook_url": "https://httpbin.org/post"
  }'
```

Ответ `202`: `payment_id`, `pending`, `created_at`. Через 2–5 секунд `GET /api/v1/payments/{id}` вернёт `succeeded` или `failed`.

## Гарантии доставки

**Outbox.** Платёж и событие в одной транзакции. Relay держит `FOR UPDATE SKIP LOCKED` до успешного publish и только потом ставит `published_at`. Если брокер недоступен, commit не будет — строка unpublished. Если publish прошёл, а commit нет, возможна повторная публикация; consumer идемпотентен.

**Шлюз.** Исход стабилен по `payment_id` (доля успеха ~90%): повтор после аварии между ответом шлюза и `UPDATE` не меняет результат. Терминальный статус не перезаписывается.

**Webhook.** CAS-claim → HTTP → CAS `webhook_sent_at`. Параллельный воркер получает `WebhookInFlightError` и делает `nack(requeue=True)` без сжигания retry-бюджета. Свежий claim чужой воркер не перехватывает; протухший (крэш, TTL 45s) — можно забрать. Клиент webhook должен быть идемпотентен по `payment_id` на случай крэша после HTTP и до `webhook_sent_at`.

**Retry / DLQ.** Ошибки обработки: попытки 1–2 в TTL-очередь, третья — `reject(requeue=False)` в `payments.new.dlq`. Нет платежа в БД — сразу DLQ, без ретраев. Prefetch = 1.

**Один consumer.** Один процесс, `Channel(prefetch_count=1)`.

## Очереди

| Очередь | Роль |
|---------|------|
| `payments.new` | входящие, DLX → DLQ |
| `payments.new.retry.1000ms` | повтор через 1s |
| `payments.new.retry.2000ms` | повтор через 2s |
| `payments.new.dlq` | окончательно упавшие, никто не читает |

## Конфиг

`.env.example`: `API_KEY`, `DATABASE_URL`, `RABBITMQ_URL`, `LOG_LEVEL`. Compose для api/consumer подставляет URL сервисов сам.
