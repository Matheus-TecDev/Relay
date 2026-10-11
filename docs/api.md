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

Queues the original event for reprocessing through the Transactional Outbox. Relay reuses the existing `Event`, preserves `correlation_id` and `trace_id`, resets the processing state to `pending`, moves the event back to `queued`, creates or resets its `OutboxMessage` to `pending`, and records the operational action.

The request returns after the database transaction is persisted. RabbitMQ publication is asynchronous and performed later by the Outbox publisher, so `200` means the event was queued in the Outbox, not that broker delivery was already confirmed. Later publication failures remain retryable by the Outbox retry flow.

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
- `409`: reprocessing blocked by an operational safety rule.

### `POST /api/dead-letter-events/reprocess-batch`

Queues several dead-letter events for reprocessing in one request. Each item goes through the same rules as the single-event endpoint, which keeps working unchanged.

Request:

```json
{
  "dead_letter_event_ids": [
    "3f0c2b1e-6f0a-4c59-9a55-0c8d4c1f2a11",
    "9b7e4d2a-1c3f-4e8b-b6a2-5d7f9e0c3b22"
  ]
}
```

`dead_letter_event_ids` must contain from 1 through 20 valid UUIDs with no duplicates. Anything else is rejected with `422` before any event is touched.

Response `200`:

```json
{
  "summary": {
    "total": 2,
    "queued": 1,
    "conflict": 1,
    "not_found": 0,
    "failed": 0
  },
  "results": [
    {
      "dead_letter_event_id": "3f0c2b1e-6f0a-4c59-9a55-0c8d4c1f2a11",
      "status": "queued",
      "reason": null,
      "message": null,
      "event_id": "event-id",
      "routing_key": "events.created",
      "correlation_id": "correlation-id",
      "trace_id": "trace-id"
    },
    {
      "dead_letter_event_id": "9b7e4d2a-1c3f-4e8b-b6a2-5d7f9e0c3b22",
      "status": "conflict",
      "reason": "not_dead_letter",
      "message": "Event is not currently in dead letter state.",
      "event_id": "other-event-id",
      "routing_key": null,
      "correlation_id": null,
      "trace_id": null
    }
  ]
}
```

`results` keeps the order of the request. Item statuses:

| Status | Meaning | `reason` values |
| --- | --- | --- |
| `queued` | The item's transaction committed and its `OutboxMessage` is `pending`. | `null` |
| `conflict` | Blocked by a safety rule; nothing changed. Equivalent to `409` on the single endpoint. | `not_dead_letter`, `already_processed`, `missing_routing_key`, `event_already_queued_in_batch` |
| `not_found` | No DLQ record has this ID. Equivalent to `404` on the single endpoint. | `dead_letter_event_not_found` |
| `failed` | An unexpected error happened and the item was rolled back. Safe to retry. | `internal_error` |

Partial success:

- A valid, authenticated request always returns `200`, even when no item was queued. Clients must read `summary` and `results`, not only the HTTP status.
- Each item runs in its own database transaction, in request order. An item that fails is rolled back and does not affect the items before or after it.
- `queued` means the event was persisted in the Outbox. RabbitMQ publication stays asynchronous and retryable, exactly as in the single endpoint; the request never publishes to the broker.
- The batch is not atomic as a whole. If the request is interrupted, items already committed stay queued.
- Repeating a request is safe: events queued by the first call are no longer in dead letter state, so they come back as `conflict` with `not_dead_letter` and are not queued again.
- An event can have several DLQ records. Whichever record is reprocessed first queues the event; the others return `conflict`, with `event_already_queued_in_batch` when the first one was in the same request.
- Concurrent batch and single requests for the same event are serialized by PostgreSQL row locks, so only one of them queues it.

Possible errors:

- `401`: missing, invalid, or expired token;
- `422`: empty list, more than 20 IDs, an ID that is not a UUID, or duplicate IDs.

## Reprocessing Semantics

- The original event is reused.
- No new `Event` is created.
- The routing key comes from `original_routing_key`, falling back to `event.routing_key`.
- `correlation_id` and `trace_id` are preserved.
- `EventProcessingState` is reset to `pending`.
- `OutboxMessage` is created or reset to `pending`.
- Reprocessing does not replace real handler idempotency.
