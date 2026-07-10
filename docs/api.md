# API

The API uses the configurable `API_V1_PREFIX`, which defaults to `/api`.

## Health

### `GET /health`

Returns the API's basic status.

```json
{
  "status": "ok"
}
```

## Authentication

Operational routes under `/api/events` and `/api/dead-letter-events` require a JWT:

```http
Authorization: Bearer ACCESS_TOKEN
```

### `POST /api/auth/login`

Authenticates the local administrative user.

Request:

```json
{
  "username": "admin",
  "password": "relay_admin"
}
```

Response `200`:

```json
{
  "access_token": "jwt-token",
  "token_type": "bearer",
  "expires_in": 28800
}
```

Possible error: `401` for invalid credentials.

### `GET /api/auth/me`

Returns the authenticated user.

```json
{
  "username": "admin"
}
```

Possible error: `401` for a missing, invalid, or expired token.

## Events

### `POST /api/events`

Creates and persists an event and writes the corresponding message to `outbox_messages`. The `relay-outbox-publisher` publishes it to the `relay.events` exchange.

Request:

```json
{
  "event_type": "customer.created",
  "payload": {
    "customer_id": "123"
  },
  "routing_key": "events.created",
  "correlation_id": "optional-correlation-id",
  "trace_id": "optional-trace-id"
}
```

Fields:

- `event_type`: required string from 1 through 120 characters;
- `payload`: required JSON object;
- `routing_key`: optional string up to 120 characters; defaults to `events.created`;
- `correlation_id`: optional string up to 120 characters;
- `trace_id`: optional string up to 120 characters.

Response `201`:

```json
{
  "id": "event-id",
  "event_type": "customer.created",
  "payload": {
    "customer_id": "123"
  },
  "routing_key": "events.created",
  "correlation_id": "correlation-id",
  "trace_id": "trace-id",
  "status": "queued",
  "created_at": "2026-01-01T12:00:00Z",
  "updated_at": "2026-01-01T12:00:00Z"
}
```

Possible errors:

- `401`: missing, invalid, or expired token;
- `503`: the event was stored, but the legacy direct RabbitMQ publishing path failed. The current reliable flow publishes through the Outbox.

### `GET /api/events`

Lists recent events for the operational dashboard.

Query parameter: `limit`, an integer from 1 through 100; default `25`.

```json
[
  {
    "id": "event-id",
    "event_type": "customer.created",
    "payload": {
      "customer_id": "123"
    },
    "routing_key": "events.created",
    "correlation_id": "correlation-id",
    "trace_id": "trace-id",
    "status": "processed",
    "created_at": "2026-01-01T12:00:00Z",
    "updated_at": "2026-01-01T12:00:05Z"
  }
]
```

Possible error: `401` for a missing, invalid, or expired token.

### `GET /api/events/summary`

Returns aggregated data for the operational dashboard.

```json
{
  "total_events": 120,
  "by_status": {
    "received": 0,
    "queued": 2,
    "processing": 1,
    "processed": 100,
    "failed": 5,
    "dead_letter": 10,
    "publish_failed": 2
  },
  "dead_letter_total": 10,
  "oldest_dead_letter_age_seconds": 1800,
  "recent_events_count": 25
}
```

When the DLQ is empty, `oldest_dead_letter_age_seconds` is `null`.

### `GET /api/events/{id}`

Returns an event with its payload, processing attempts, logs, and related DLQ records.

The response includes the event fields plus:

- `attempts`: attempt number, state, error, and start/finish timestamps;
- `logs`: level, message, metadata, and timestamp;
- `dead_letter_entries`: reason, payload, retry count, original routing key, and error.

Possible errors:

- `401`: missing, invalid, or expired token;
- `404`: event not found.

## Dead Letter Events

### `GET /api/dead-letter-events`

Lists events sent to the DLQ.

Query parameter: `limit`, an integer from 1 through 100; default `50`.

```json
[
  {
    "id": "dead-letter-id",
    "event_id": "event-id",
    "reason": "max_retries_exceeded",
    "error_message": "handler error",
    "retry_count": 3,
    "original_routing_key": "events.created",
    "created_at": "2026-01-01T12:10:00Z",
    "correlation_id": "correlation-id",
    "trace_id": "trace-id",
    "event_status": "dead_letter"
  }
]
```

### `GET /api/dead-letter-events/{id}`

Returns operational details for a dead-letter event, including its payload, original event, attempts, and logs.

Possible errors:

- `401`: missing, invalid, or expired token;
- `404`: DLQ event not found.

### `POST /api/dead-letter-events/{id}/reprocess`

Republishes the original event to the main exchange with its original routing key. Relay preserves `correlation_id` and `trace_id`, moves the original event back to `queued`, and records the operational action.

```json
{
  "dead_letter_event_id": "dead-letter-id",
  "event_id": "event-id",
  "status": "queued",
  "routing_key": "events.created",
  "correlation_id": "correlation-id",
  "trace_id": "trace-id"
}
```

Possible errors:

- `401`: missing, invalid, or expired token;
- `404`: DLQ event not found;
- `409`: reprocessing blocked by an operational safety rule;
- `503`: event could not be republished.

## Reprocessing Semantics

- The original event is reused.
- No new `Event` is created.
- The routing key comes from `original_routing_key`, falling back to `event.routing_key`.
- `correlation_id` and `trace_id` are preserved.
- Reprocessing does not replace real handler idempotency.
