# API

A API usa o prefixo configurável `API_V1_PREFIX`, com padrão `/api`.

## Health

### `GET /health`

Retorna o status básico da API.

Resposta esperada:

```json
{
  "status": "ok"
}
```

## Autenticação

As rotas operacionais em `/api/events` e `/api/dead-letter-events` exigem JWT no header:

```http
Authorization: Bearer ACCESS_TOKEN
```

### `POST /api/auth/login`

Autentica o usuário administrativo local.

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

Possível erro:

- `401`: credenciais inválidas.

### `GET /api/auth/me`

Retorna o usuário autenticado.

Response `200`:

```json
{
  "username": "admin"
}
```

Possível erro:

- `401`: token ausente, inválido ou expirado.

## Eventos

### `POST /api/events`

Cria e persiste um evento, registrando a mensagem correspondente em `outbox_messages`. A publicação na exchange `relay.events` é feita pelo `relay-outbox-publisher`.

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

Campos:

- `event_type`: string obrigatória, entre 1 e 120 caracteres.
- `payload`: objeto JSON obrigatório.
- `routing_key`: string opcional, até 120 caracteres. Se ausente, o backend usa `events.created`.
- `correlation_id`: string opcional, até 120 caracteres.
- `trace_id`: string opcional, até 120 caracteres.

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

Possível erro:

- `401`: token ausente, inválido ou expirado.
- `503`: evento armazenado, mas publicação direta/legada para RabbitMQ falhou. No fluxo atual, a publicação confiável ocorre pela outbox.

### `GET /api/events`

Lista eventos recentes para alimentar o dashboard operacional.

Query params:

- `limit`: inteiro entre 1 e 100. Padrão: `25`.

Response:

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

Possível erro:

- `401`: token ausente, inválido ou expirado.

### `GET /api/events/summary`

Retorna uma visão agregada para o dashboard operacional.

Response:

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

Quando não houver eventos em DLQ, `oldest_dead_letter_age_seconds` retorna `null`.

Possível erro:

- `401`: token ausente, inválido ou expirado.

### `GET /api/events/{id}`

Mostra detalhes de um evento, incluindo payload, tentativas, logs e registros relacionados de DLQ.

Response:

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
  "status": "dead_letter",
  "created_at": "2026-01-01T12:00:00Z",
  "updated_at": "2026-01-01T12:10:00Z",
  "attempts": [
    {
      "id": "attempt-id",
      "event_id": "event-id",
      "attempt_number": 1,
      "status": "failed",
      "error_message": "handler error",
      "started_at": "2026-01-01T12:00:01Z",
      "finished_at": "2026-01-01T12:00:02Z"
    }
  ],
  "logs": [
    {
      "id": "log-id",
      "event_id": "event-id",
      "level": "error",
      "message": "Event failed",
      "log_metadata": {
        "reason": "handler error"
      },
      "created_at": "2026-01-01T12:00:02Z"
    }
  ],
  "dead_letter_entries": [
    {
      "id": "dead-letter-id",
      "event_id": "event-id",
      "reason": "max_retries_exceeded",
      "payload": {
        "customer_id": "123"
      },
      "retry_count": 3,
      "original_routing_key": "events.created",
      "error_message": "handler error",
      "created_at": "2026-01-01T12:10:00Z"
    }
  ]
}
```

Possível erro:

- `401`: token ausente, inválido ou expirado.
- `404`: evento não encontrado.

## Dead Letter Events

### `GET /api/dead-letter-events`

Lista eventos que foram enviados para DLQ.

Query params:

- `limit`: inteiro entre 1 e 100. Padrão: `50`.

Response:

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

Possível erro:

- `401`: token ausente, inválido ou expirado.

### `GET /api/dead-letter-events/{id}`

Mostra detalhes operacionais do evento morto, incluindo payload, evento original, tentativas e logs.

Response:

```json
{
  "id": "dead-letter-id",
  "event_id": "event-id",
  "reason": "max_retries_exceeded",
  "payload": {
    "customer_id": "123"
  },
  "retry_count": 3,
  "original_routing_key": "events.created",
  "error_message": "handler error",
  "created_at": "2026-01-01T12:10:00Z",
  "event": {
    "id": "event-id",
    "event_type": "customer.created",
    "payload": {
      "customer_id": "123"
    },
    "routing_key": "events.created",
    "correlation_id": "correlation-id",
    "trace_id": "trace-id",
    "status": "dead_letter",
    "created_at": "2026-01-01T12:00:00Z",
    "updated_at": "2026-01-01T12:10:00Z"
  },
  "attempts": [],
  "logs": []
}
```

Possível erro:

- `401`: token ausente, inválido ou expirado.
- `404`: evento de DLQ não encontrado.

### `POST /api/dead-letter-events/{id}/reprocess`

Republica o evento original na exchange principal usando a routing key original. O Relay preserva `correlation_id` e `trace_id`, atualiza o evento original para `queued` e registra a ação operacional.

Response:

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

Possíveis erros:

- `401`: token ausente, inválido ou expirado.
- `404`: evento de DLQ não encontrado.
- `409`: reprocessamento bloqueado por regra de segurança operacional.
- `503`: evento não foi republicado.

## Semântica de Reprocessamento

- O evento original é reutilizado.
- Nenhum novo `Event` é criado.
- A routing key vem de `original_routing_key`, com fallback para `event.routing_key`.
- `correlation_id` e `trace_id` são preservados.
- A operação não substitui idempotência real no handler.
