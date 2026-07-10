# Architecture

Relay is a full-stack asynchronous event-processing platform. The API receives events, persists their state in PostgreSQL, and writes a message to `outbox_messages` in the same transaction. A separate publisher sends persistent messages to the `relay.events` topic exchange, and independent domain workers consume their respective queues.

## End-to-End Flow

```text
Client
  -> FastAPI /api/events
  -> PostgreSQL events + event_processing_states + outbox_messages
  -> relay-outbox-publisher
  -> RabbitMQ relay.events
  -> relay.events.audit | relay.events.analytics | relay.events.notifications
  -> domain workers
  -> retry queues or relay.events.dead_letter
  -> PostgreSQL attempts, logs, and DLQ records
  -> Prometheus, Grafana, Loki, Tempo, and Alertmanager
```

![Relay Architecture](../assets/architecture.png)

Every event carries:

- `correlation_id`: correlates events in the same business flow; accepted from the API or generated automatically;
- `trace_id`: tracks the processing lifecycle; accepted from the API, derived from the active OpenTelemetry context, or generated automatically.

Both identifiers are stored in PostgreSQL, included in the Outbox message, and propagated through RabbitMQ.

## Components

| Component | Responsibility |
| --- | --- |
| FastAPI backend | Receives events, exposes operational state, and publishes metrics |
| PostgreSQL | Stores events, Outbox messages, attempts, logs, DLQ records, and idempotency state |
| `relay-outbox-publisher` | Publishes pending Outbox messages to the main exchange |
| RabbitMQ | Routes domain events with a topic exchange, DLX, and retry queues |
| Workers | Process domain messages and enforce retry, DLQ, and idempotency rules |
| Redis | Auxiliary cache and infrastructure |
| React frontend | Provides event and DLQ operations |
| Observability stack | Prometheus, Grafana, Loki, Tempo, OpenTelemetry, Alloy, and Alertmanager |

## RabbitMQ Topology

Exchanges:

- main topic exchange: `relay.events`;
- Dead Letter Exchange: `relay.events.dlx`;
- both durable, with persistent messages.

Domain queues:

- `relay.events.audit`;
- `relay.events.analytics`;
- `relay.events.notifications`.

Retry queues:

- `relay.events.retry.10s`;
- `relay.events.retry.30s`;
- `relay.events.retry.5m`.

Dead-letter queue:

- `relay.events.dead_letter`.

Main bindings:

- `audit.*` and `events.*` -> `relay.events.audit`;
- `analytics.*` -> `relay.events.analytics`;
- `notifications.*` -> `relay.events.notifications`;
- `retry.*` -> domain queues after the retry TTL expires.

DLX bindings:

- `retry.10s` -> `relay.events.retry.10s`;
- `retry.30s` -> `relay.events.retry.30s`;
- `retry.5m` -> `relay.events.retry.5m`;
- `dead_letter.#` -> `relay.events.dead_letter`.

Example routing keys include `events.created`, `audit.created`, `analytics.page_viewed`, and `notifications.email_requested`.

## Transactional Outbox

Relay uses the Transactional Outbox pattern so event creation and publication intent are committed atomically. The API never publishes directly to RabbitMQ in the current flow.

```text
API -> PostgreSQL events + outbox_messages -> relay-outbox-publisher -> RabbitMQ -> workers
```

A single event-creation transaction persists:

- `events`;
- `event_processing_states`;
- `outbox_messages`.

Outbox states:

- `pending`: stored and waiting for publication;
- `publishing`: locked by a publisher;
- `published`: successfully sent to RabbitMQ;
- `failed`: publication failed and a retry was scheduled.

The publisher reads batches, acquires transactional locks, publishes to `relay.events`, and updates Outbox state. It uses `SELECT ... FOR UPDATE SKIP LOCKED` where supported, allowing multiple publishers without processing the same row concurrently.

Failure handling records `attempt_count`, `last_error`, `last_attempt_at`, and `next_attempt_at`. Messages left in `publishing` after a publisher crash become eligible for recovery after `OUTBOX_PUBLISHING_TIMEOUT_SECONDS`.

Relevant configuration:

```env
OUTBOX_PUBLISHER_NAME=relay-outbox-publisher
OUTBOX_BATCH_SIZE=10
OUTBOX_POLL_INTERVAL_SECONDS=2
OUTBOX_PUBLISHING_TIMEOUT_SECONDS=300
OUTBOX_BACKOFF_SECONDS=5,30,120,300
OUTBOX_METRICS_PORT=9101
```

The Outbox guarantees reliable publication intent, but not exactly-once delivery. A crash after RabbitMQ accepts a message and before PostgreSQL commits `published` may cause duplicate publication. Idempotent consumers protect Relay from duplicate handler execution; external side effects must also receive an idempotency key, normally `event_id`.

## Workers

| Service | Source | Accepted routing keys | Entry point |
| --- | --- | --- | --- |
| `relay-outbox-publisher` | `outbox_messages` | N/A | `python -m app.workers.outbox_publisher` |
| `relay-audit-worker` | `relay.events.audit` | `events.*`, `audit.*` | `python -m app.workers.audit_consumer` |
| `relay-analytics-worker` | `relay.events.analytics` | `analytics.*` | `python -m app.workers.analytics_consumer` |
| `relay-notification-worker` | `relay.events.notifications` | `notifications.*` | `python -m app.workers.notification_consumer` |

Per-worker variables:

- `WORKER_NAME`;
- `WORKER_QUEUE`;
- `WORKER_ROUTING_KEY`.

Domain handlers remain isolated as `audit_worker`, `analytics_worker`, and `notification_worker`.

Workers can scale independently:

```bash
docker compose up --scale relay-analytics-worker=3
docker compose up --scale relay-audit-worker=2 --scale relay-notification-worker=2
```

Shared retry queues return messages through `retry.*`. Workers inspect `x-original-routing-key`, and only the compatible worker invokes the handler.

## Retry and Backoff

Workers avoid `basic_nack(requeue=True)`, preventing tight loops in the main queue. On failure, the original message is republished to the DLX with `x-retry-count` and the appropriate retry routing key, then acknowledged.

Failure progression:

1. first failure -> `relay.events.retry.10s`, `x-retry-count=1`;
2. second failure -> `relay.events.retry.30s`, `x-retry-count=2`;
3. third failure -> `relay.events.retry.5m`, `x-retry-count=3`;
4. fourth failure -> `relay.events.dead_letter`, event state `dead_letter`, and a `dead_letter_events` record.

After each TTL expires, RabbitMQ returns the message to `relay.events`. The `x-original-routing-key` header preserves domain routing.

## Dead Letter Queue

The operational DLQ stores messages that exhausted automated retries. PostgreSQL retains the history, and the API supports:

- `GET /api/dead-letter-events`: list DLQ events with failure and correlation data;
- `GET /api/dead-letter-events/{id}`: inspect payload, original event, attempts, and logs;
- `POST /api/dead-letter-events/{id}/reprocess`: republish the existing event.

Manual reprocessing preserves `correlation_id`, `trace_id`, and `original_routing_key`, moves the original event to `queued`, and creates an operational `EventLog`. It does not create a second `Event`.

Recommended operation:

1. inspect the DLQ record, payload, attempts, and logs;
2. correct the root cause;
3. request reprocessing;
4. track the event from `queued` through its domain worker.

Repeated reprocessing is blocked for a short window, but this safeguard does not replace handler idempotency.

## Idempotency

Workers use `event_processing_states` to prevent the same `event_id` from executing a handler more than once inside Relay.

States:

- `pending`: created but not processed;
- `processing`: locked by a worker;
- `processed`: completed; duplicates are acknowledged and skipped;
- `failed`: failed and eligible for retry;
- `dead_lettered`: retries exhausted.

Processing algorithm:

1. begin a transaction and lock the event state with `SELECT ... FOR UPDATE`;
2. skip and acknowledge `processed` or `dead_lettered` events;
3. skip active `processing` states that have not timed out;
4. acquire `pending`, `failed`, or stale `processing` states;
5. record the attempt and run the handler;
6. commit `processed` on success;
7. commit `failed` and choose retry or DLQ on error.

```env
IDEMPOTENCY_PROCESSING_TIMEOUT_SECONDS=900
```

This protects against RabbitMQ redelivery, retry of processed events, concurrent workers, lost connections, worker crashes before commit, and duplicate Outbox publication. External systems must still implement idempotency for their own side effects.

## Status Values

Event states in `backend/app/core/enums.py`:

- `received`;
- `queued`;
- `processing`;
- `processed`;
- `failed`;
- `dead_letter`;
- `publish_failed`.

Standard log levels: `info`, `warning`, and `error`.

## Project Structure

```text
backend/
  app/
    api/routes/
    core/
    db/
    models/
    observability/
    queues/
    schemas/
    services/
    workers/
  alembic/
  tests/
frontend/
  src/
    api/
    components/
    hooks/
    pages/
    styles/
    types/
infra/
  alertmanager/
  alloy/
  grafana/
  loki/
  nginx/
  otel-collector/
  prometheus/
  tempo/
```

## Local Development without Docker

Use Python 3.12 to match `backend/Dockerfile`.

Backend:

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

For local execution without Docker, set `DATABASE_URL`, `RABBITMQ_*`, and `REDIS_URL` for the local environment.
