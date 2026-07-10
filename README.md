# Relay

Relay is a full-stack platform for asynchronous event processing, built with FastAPI, RabbitMQ, PostgreSQL, Redis, React, Docker, and a complete observability stack.

The project demonstrates how an application can receive events, persist state reliably, process domain-specific messages, and provide operational visibility into failures, retries, the Dead Letter Queue, metrics, logs, and traces.

## Technology Stack

| Area | Technologies |
| --- | --- |
| Backend | Python, FastAPI, SQLAlchemy, Alembic, Pydantic |
| Frontend | React, TypeScript, Vite |
| Messaging | RabbitMQ, topic exchange, DLX, retry queues |
| Data | PostgreSQL, Redis |
| Observability | Prometheus, Grafana, OpenTelemetry, Tempo, Loki, Alloy, Alertmanager |
| Infrastructure | Docker Compose, Nginx |

## Problem

Event-driven systems must publish messages reliably, process asynchronous workloads without duplicate side effects, and provide a clear path for failure investigation. Relay models this scenario with a production-oriented architecture that separates the API, reliable publishing, domain workers, and operational tooling.

## Features

- Event creation and listing.
- Reliable publishing through the Transactional Outbox pattern.
- Domain-specific asynchronous processing.
- Progressive retry backoff.
- Dead Letter Queue inspection and manual reprocessing.
- Idempotent consumers.
- Correlation through `correlation_id` and `trace_id`.
- Operational web dashboard with filters, pagination, event details, and DLQ operations.
- JWT authentication for the operational interface.
- CI validation for backend, frontend, and Docker Compose.
- Metrics, centralized logs, distributed tracing, and alerting.

## Architecture

```text
API -> PostgreSQL + Outbox -> Outbox Publisher -> RabbitMQ -> Workers -> Retry/DLQ -> Observability Stack
```

![Relay Architecture](assets/architecture.png)

## Running Locally

```bash
cp .env.example .env
docker compose up --build
```

Main URLs:

- Application: http://localhost
- API: http://localhost:8000
- Health check: http://localhost/health
- RabbitMQ Management: http://localhost:15672
- Prometheus: http://localhost:9090
- Alertmanager: http://localhost:9093
- Grafana: http://localhost:3000

Default local credentials:

- Application: `admin` / `relay_admin`
- RabbitMQ: `relay` / `relay_dev_password`
- Grafana: `relay` / `relay_dev_password`

## Endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/health` | Checks API health |
| `POST` | `/api/auth/login` | Authenticates the operator |
| `GET` | `/api/auth/me` | Returns the authenticated user |
| `POST` | `/api/events` | Creates an event |
| `GET` | `/api/events` | Lists recent events |
| `GET` | `/api/events/summary` | Aggregates events by status and DLQ state |
| `GET` | `/api/events/{id}` | Returns event details, attempts, logs, and DLQ records |
| `GET` | `/api/dead-letter-events` | Lists DLQ events |
| `GET` | `/api/dead-letter-events/{id}` | Returns DLQ event details |
| `POST` | `/api/dead-letter-events/{id}/reprocess` | Reprocesses a dead-letter event |

## Dashboard

![Relay Dashboard](assets/dashboard.png)

## Project Structure

```text
backend/   API, models, services, workers, and instrumentation
frontend/  React operational dashboard
infra/     Nginx, Prometheus, Grafana, Loki, Tempo, Alloy, and Alertmanager
docs/      Additional technical documentation
assets/    Documentation images
```

## Documentation

| Document | Coverage |
| --- | --- |
| [Architecture](docs/architecture.md) | System flow, RabbitMQ, Outbox, retry, DLQ, idempotency, and workers |
| [Observability](docs/observability.md) | Prometheus, Grafana, metrics, logs, tracing, and alerts |
| [API](docs/api.md) | Endpoints, payloads, responses, and reprocessing |
