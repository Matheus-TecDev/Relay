# Observability

Relay includes a local stack for observing the API, Outbox publisher, domain workers, RabbitMQ, and supporting infrastructure.

## Stack

- **Prometheus:** scrapes the API, `relay-outbox-publisher`, workers, and RabbitMQ Exporter.
- **Grafana:** provides version-controlled dashboards for application metrics, RabbitMQ, tracing, logs, alerts, Outbox, and idempotency.
- **RabbitMQ Exporter:** reads broker metrics through the RabbitMQ Management API.
- **Loki:** stores centralized logs locally.
- **Alloy:** discovers Docker containers, collects stdout/stderr, and sends records to Loki.
- **OpenTelemetry Collector:** receives OTLP traces.
- **Tempo:** stores traces and makes them queryable from Grafana.
- **Alertmanager:** receives Prometheus alerts and routes them by severity.

## Local Endpoints

- Prometheus: http://localhost:9090
- Alertmanager: http://localhost:9093
- Grafana: http://localhost:3000
- API metrics: http://localhost:8000/metrics
- RabbitMQ Exporter: http://localhost:9419/metrics
- Tempo: http://localhost:3200
- OTLP gRPC: `localhost:4317`
- OTLP HTTP: `localhost:4318`
- Loki: http://localhost:3100
- Alloy UI: http://localhost:12345

Default local Grafana credentials are `relay` / `relay_dev_password`. They are development-only and can be changed with `GRAFANA_ADMIN_USER` and `GRAFANA_ADMIN_PASSWORD`.

## Prometheus

Relay exposes metrics at `GET /metrics`. Docker Compose scrapes:

- backend at `backend:8000/metrics`;
- publisher at `relay-outbox-publisher:9101/metrics`;
- workers at `relay-*-worker:9102/metrics`;
- RabbitMQ Exporter at `rabbitmq-exporter:9419/metrics`.

### Main Application Metrics

| Metric | Meaning |
| --- | --- |
| `relay_events_created_total` | Events accepted by the API |
| `relay_events_published_total` | Events published to RabbitMQ |
| `relay_event_publish_failures_total` | RabbitMQ publishing failures |
| `relay_event_creation_duration_seconds` | Event and Outbox creation duration |
| `relay_worker_events_processed_total` | Successfully processed events by worker |
| `relay_worker_events_failed_total` | Worker processing failures |
| `relay_worker_events_retried_total` | Events routed to retry queues |
| `relay_worker_events_dead_lettered_total` | Events sent to the DLQ |
| `relay_worker_events_duplicate_skipped_total` | Messages skipped by idempotency |
| `relay_idempotency_lock_failures_total` | Processing-lock acquisition failures |
| `relay_idempotency_stale_processing_events_total` | Events exceeding the processing timeout |
| `relay_outbox_messages_pending_total` | Outbox messages waiting for publication |
| `relay_outbox_messages_failed_total` | Failed Outbox messages waiting for retry |
| `relay_outbox_messages_published_total` | Published Outbox messages |
| `relay_outbox_publish_failures_total` | Outbox publication failures |
| `relay_outbox_messages_stuck_publishing_total` | Messages stuck in `publishing` |
| `relay_outbox_oldest_pending_age_seconds` | Age of the oldest pending or failed message |
| `relay_outbox_retries_total` | Outbox retries scheduled |
| `relay_event_processing_duration_seconds` | Worker processing duration |
| `relay_dead_letter_events_total` | Current DLQ size |
| `relay_dead_letter_oldest_event_age_seconds` | Age of the oldest DLQ event |
| `relay_dead_letter_reprocess_total` | Manual reprocessing requests |
| `relay_dead_letter_reprocess_failures_total` | Failed or blocked reprocessing requests |
| `relay_events_by_status` | Current events by status |
| `relay_event_attempts_by_status` | Current attempts by status |

API and manual-reprocessing counters are incremented in application flows. Current status, attempt, DLQ, Outbox, and idempotency gauges are derived from PostgreSQL at scrape time. Worker metrics are emitted by consumer processes.

Example PromQL:

```promql
sum(rate(relay_events_created_total[5m]))
sum(rate(relay_events_published_total[5m])) by (routing_key)
sum(rate(relay_worker_events_failed_total[5m])) by (worker_name, routing_key)
sum(rate(relay_worker_events_retried_total[5m])) by (retry_queue)
relay_dead_letter_events_total
relay_dead_letter_oldest_event_age_seconds
relay_outbox_messages_pending_total
relay_outbox_messages_stuck_publishing_total
relay_outbox_oldest_pending_age_seconds
histogram_quantile(0.95, sum(rate(relay_event_processing_duration_seconds_bucket[5m])) by (le, routing_key))
```

## Grafana

Provisioned dashboards:

- `Relay Observability`: throughput, publishing failures, processing, retries, DLQ, reprocessing, p95 latency, and event states;
- `Relay RabbitMQ`: ready and unacknowledged messages, consumers, publish/delivery rate, DLQ, retry queues, connections, channels, and memory;
- `Relay Tracing`: trace volume, average latency, p95/p99, endpoint errors, operation duration, and recent traces;
- `Relay Logs`: log volume, errors, recent records, and correlation filters;
- `Relay Alerts`: active application and infrastructure alerts;
- `Relay Outbox & Idempotency`: publisher health, Outbox state, duplicate skips, lock failures, and stale processing.

Dashboard files are stored under `infra/grafana/dashboards/`.

## RabbitMQ Exporter

- Compose service: `rabbitmq-exporter`;
- image: `kbudde/rabbitmq-exporter:1.0.0`;
- internal RabbitMQ URL: `http://rabbitmq:15672`;
- local metrics endpoint: http://localhost:9419/metrics;
- Prometheus job: `rabbitmq-exporter`.

Useful metrics:

```promql
rabbitmq_queue_messages_ready
rabbitmq_queue_messages_unacknowledged
rabbitmq_queue_consumers
sum(rate(rabbitmq_queue_messages_published_total[5m])) by (queue)
sum(rate(rabbitmq_queue_messages_delivered_total[5m])) by (queue)
rabbitmq_queue_messages{queue="relay.events.dead_letter"}
rabbitmq_queue_messages{queue=~"relay\.events\.retry\..*"}
rabbitmq_connections
rabbitmq_channels
rabbitmq_node_mem_used
```

## Distributed Tracing

```text
FastAPI / Workers -> OTLP gRPC -> OpenTelemetry Collector -> Tempo -> Grafana
```

Instrumentation:

- `opentelemetry-instrumentation-fastapi`;
- `opentelemetry-instrumentation-sqlalchemy`;
- `opentelemetry-instrumentation-pika`;
- `opentelemetry-exporter-otlp-proto-grpc`.

Main configuration:

```env
OTEL_ENABLED=true
OTEL_SERVICE_NAME=relay-backend
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_INSECURE=true
OTEL_TRACES_SAMPLER=always_on
OTEL_RESOURCE_ATTRIBUTES=service.namespace=relay,deployment.environment=development
```

Workers use their own service names: `relay-audit-worker`, `relay-analytics-worker`, and `relay-notification-worker`.

The business `trace_id` is preserved. If an incoming event has no `trace_id`, the backend uses the active OpenTelemetry trace ID when available or generates a UUID. `correlation_id` remains independent.

Example TraceQL Metrics:

```traceql
{ resource.service.namespace = "relay" } | rate()
{ resource.service.namespace = "relay" } | avg_over_time(duration)
{ resource.service.namespace = "relay" } | quantile_over_time(duration, .95)
{ resource.service.name = "relay-backend" && status = error } | rate() by (span.http.route)
```

Tempo uses local storage and has no adaptive or tail sampling. Dashboards require actual ingested traces.

## Centralized Logs

```text
Docker containers -> Alloy -> Loki -> Grafana
```

Alloy discovers containers through `/var/run/docker.sock` and collects application and infrastructure logs. The backend writes JSON to stdout with:

- `timestamp`;
- `level`;
- `service`;
- `environment`;
- `trace_id`;
- `span_id`;
- `correlation_id`;
- `event_id`;
- `endpoint`;
- `logger`;
- `message`.

Grafana's Loki data source defines a `TraceID` derived field so a log containing `trace_id` can open the corresponding Tempo trace.

Example LogQL:

```logql
{job="relay/docker"}
{job="relay/docker", service="backend"} | json
{job="relay/docker"} | json | trace_id="TRACE_ID"
{job="relay/docker"} | json | correlation_id="CORRELATION_ID"
{job="relay/docker", service=~"relay-.*-worker"} | json | event_id="EVENT_ID"
{job="relay/docker"} |~ "(?i)(dlq|dead letter|retry|publish failed|failed to publish)"
```

High-cardinality identifiers stay inside the JSON payload rather than becoming Loki labels. Third-party logs may not be JSON but remain searchable by container and service.

## Alerts

Prometheus loads version-controlled rules from `infra/prometheus/rules/relay-alerts.yml` and sends them to Alertmanager.

### Application and DLQ

- `RelayDeadLetterQueueHasEvents` (`warning`);
- `RelayDeadLetterQueueGrowing` (`critical`);
- `RelayOldDeadLetterEvent` (`warning`);
- `RelayHighRetryRate` (`warning`);
- `RelayHighWorkerFailureRate` (`warning`);
- `RelayEventPublishFailures` (`critical`).

### RabbitMQ and Scraping

- `RelayQueueWithoutConsumers` (`critical`);
- `RelayQueueBacklogGrowing` (`warning`);
- `RelayHighUnackedMessages` (`warning`);
- `RelayRetryQueueBacklog` (`warning`);
- `RelayRabbitMQExporterDown` (`critical`);
- `RelayBackendMetricsDown` (`critical`).

### Outbox

- `RelayOutboxPendingTooLong` (`warning`);
- `RelayOutboxPublishFailures` (`critical`);
- `RelayOutboxHighRetryRate` (`warning`);
- `RelayOutboxStuckPublishing` (`critical`);
- `RelayOutboxPublisherDown` (`critical`).

### Idempotency

- `RelayDuplicateEventsSkipped` (`info`);
- `RelayIdempotencyLockFailures` (`warning`);
- `RelayStaleProcessingEvents` (`critical`).

Rules use `rate` and `increase` for counters and `max_over_time` or `min_over_time` for gauges.

Severity routing:

- `critical` -> `webhook-critical`;
- `warning` -> `webhook-warning`;
- `info` -> `webhook-info`.

The configured endpoints under `host.docker.internal:8080/alerts/*` are generic local-development receivers; no real secrets are stored in the repository.

## Incident Investigation

For Outbox incidents:

1. inspect publisher health, pending, failed, stuck, and oldest-message panels;
2. query publisher logs by `outbox_message_id`, `event_id`, `correlation_id`, or `trace_id`;
3. inspect `outbox_messages` state, attempts, errors, schedule, and lock data;
4. validate RabbitMQ connectivity, credentials, and the `relay.events` exchange;
5. for stuck publication, confirm publisher recovery moved stale messages to `failed`.

For idempotency incidents:

1. inspect duplicate-skip, lock-failure, and stale-processing panels;
2. query worker logs by event, correlation, or trace ID;
3. inspect `event_processing_states`, worker ownership, attempts, and timestamps;
4. identify RabbitMQ redelivery, manual retry, or duplicate Outbox publication;
5. validate the responsible worker and any partial handler failure.

## Validation

```bash
docker compose run --rm --entrypoint promtool prometheus check rules /etc/prometheus/rules/relay-alerts.yml
docker compose run --rm --entrypoint amtool alertmanager check-config /etc/alertmanager/alertmanager.yml
docker compose run --rm --entrypoint /otelcol-contrib otel-collector validate --config=/etc/otelcol-contrib/config.yml
docker compose run --rm --entrypoint /tempo tempo --config.file=/etc/tempo/tempo.yml --config.verify=true
docker compose run --rm --entrypoint /usr/bin/loki loki --config.file=/etc/loki/loki.yml --verify-config=true
docker compose run --rm --entrypoint /bin/alloy alloy validate /etc/alloy/config.alloy
```

## Current Limitations and Next Steps

- Tempo and Loki use local development storage.
- Scaling several containers of the same worker service requires Prometheus target discovery to include every replica.
- PostgreSQL-derived gauges are exposed through the backend scrape.
- Production evolution should add robust service discovery, real notification receivers, additional infrastructure dashboards, and Loki-based alert rules.
