# Payments

Тестовое: асинхронный сервис оплаты.

Принимает платёж по HTTP, кладёт его в Postgres вместе с outbox-записью, из outbox событие уходит в Rabbit (`payments.new`). Отдельный consumer эмулирует эквайринг (2-5 сек, около 90% успеха) и дергает webhook.

Суммы в рублях, два знака после запятой. `currency` по умолчанию `RUB`. USD/EUR в API тоже есть, просто не акцентировал.

## Как запустить

Нужны docker и compose.

```bash
cp .env.example .env
docker compose up --build
```

Поднятие в первый раз чуть подождёт postgres/rabbit.

- swagger: http://localhost:8000/docs
- rabbit management: http://localhost:15673 (guest / guest)

Порты с хоста специально не стандартные: postgres `5433`, amqp `5673`, ui `15673`. У меня локально 5432/5672 уже были заняты. Внутри сети контейнеров всё как обычно, `postgres:5432` и `rabbitmq:5672`.

Остановить: `docker compose down`. Volume с базой останется.

## API

На всех эндпоинтах нужен заголовок `X-API-Key`. В `.env` лежит `dev-api-key`, если не меняли.

На создании ещё нужен `Idempotency-Key`. Без него 422. Повтор с тем же ключом и тем же телом вернёт тот же платёж, если тело другое то 409.

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

Ответ 202, в теле `payment_id` и `pending`. Потом:

```bash
curl -s http://localhost:8000/api/v1/payments/<payment_id> \
  -H "X-API-Key: dev-api-key"
```

Статус меняется через пару секунд на `succeeded` или `failed`.

Для webhook я обычно подставляю url с webhook.site, так проще увидеть payload.

## Очереди / ретраи

Outbox крутится фоном в api-процессе: читает неопубликованное, `SKIP LOCKED`, публикует, пишет `published_at`.

Consumer один. Если обработка упала (в основном это webhook), сообщение уходит в ttl-очередь: сначала 1с, потом 2с. После третьей попытки в `payments.new.dlq`. Эту очередь никто не читает, смотреть в management UI.

Сам webhook ретраится отдельно, тоже три раза.

Повторная доставка из кролика шлюз второй раз не дергает, если платёж уже не `pending`. Webhook при этом может уйти ещё раз, пока не проставится `webhook_sent_at`.

## Конфиг

Всё через env, шаблон в `.env.example`:

- `API_KEY`
- `DATABASE_URL`
- `RABBITMQ_URL`
- `LOG_LEVEL`

В compose для api/consumer url'ы на сервисы переопределяются, так что локальный `.env` с `localhost` на работу в докере не влияет.
