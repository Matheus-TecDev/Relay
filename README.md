# Relay

Relay é uma plataforma full stack de processamento assíncrono de eventos com FastAPI, RabbitMQ, PostgreSQL, Redis, Docker e React. O projeto evolui de uma fila simples para uma arquitetura baseada em exchange topic do RabbitMQ, com roteamento por domínio, status padronizados, `correlation_id` e `trace_id`.

## Stack

- Backend: Python, FastAPI, SQLAlchemy, Alembic e Pydantic
- Mensageria: RabbitMQ com exchange topic
- Cache: Redis
- Banco de dados: PostgreSQL
- Frontend: React, TypeScript e Vite
- Infra: Docker Compose e Nginx
- Observabilidade futura: Prometheus, Grafana e Loki
- CI futura: GitHub Actions

## Arquitetura Atual

O backend recebe eventos via `POST /api/events`, persiste o registro no PostgreSQL e publica uma mensagem persistente na exchange topic `relay.events`. A `routing_key` pode vir no payload da API; se não vier, o backend usa `events.created`.

Cada evento recebe:

- `correlation_id`: usado para correlacionar eventos de um mesmo fluxo de negócio. Pode vir da API ou ser gerado automaticamente.
- `trace_id`: usado para rastrear o ciclo de processamento. Pode vir da API ou ser gerado automaticamente.

Ambos são salvos no banco e enviados na mensagem publicada no RabbitMQ.

## RabbitMQ

Exchanges:

- Principal: `relay.events`
- Dead Letter Exchange: `relay.events.dlx`
- Tipo: `topic`
- Duráveis
- Mensagens persistentes

Filas de domínio:

- `relay.events.audit`
- `relay.events.analytics`
- `relay.events.notifications`

Fila de dead letter:

- `relay.events.dead_letter`

Filas de retry:

- `relay.events.retry.10s`
- `relay.events.retry.30s`
- `relay.events.retry.5m`

Bindings principais:

- `audit.*` e `events.*` -> `relay.events.audit`
- `analytics.*` -> `relay.events.analytics`
- `notifications.*` -> `relay.events.notifications`
- `retry.*` -> `relay.events.audit`

Bindings na DLX:

- `retry.10s` -> `relay.events.retry.10s`
- `retry.30s` -> `relay.events.retry.30s`
- `retry.5m` -> `relay.events.retry.5m`
- `dead_letter.#` -> `relay.events.dead_letter`

Exemplos de routing keys:

- `events.created`
- `audit.created`
- `analytics.page_viewed`
- `notifications.email_requested`

### Retry, Backoff e DLQ

O worker não usa `basic_nack(requeue=True)` para retry, evitando loop infinito na fila principal. Quando uma tentativa falha, a mensagem original é publicada novamente na Dead Letter Exchange com o header RabbitMQ `x-retry-count` e a routing key de retry adequada. Depois disso, a mensagem original recebe `basic_ack`.

Fluxo de falha:

- Falha 1: publica em `relay.events.retry.10s` com `x-retry-count=1`.
- Falha 2: publica em `relay.events.retry.30s` com `x-retry-count=2`.
- Falha 3: publica em `relay.events.retry.5m` com `x-retry-count=3`.
- Falha 4: publica na DLQ `relay.events.dead_letter`, marca o evento como `dead_letter` e registra em `dead_letter_events`.

As filas de retry possuem TTL:

- `relay.events.retry.10s`: 10 segundos.
- `relay.events.retry.30s`: 30 segundos.
- `relay.events.retry.5m`: 5 minutos.

Quando o TTL expira, a mensagem volta para a exchange principal `relay.events`. O header `x-original-routing-key` preserva a routing key original do evento para que o worker resolva o handler correto mesmo quando a mensagem retorna a partir de uma fila de retry.

Diferença entre retry e DLQ:

- Retry é uma tentativa controlada de reprocessar uma falha recuperável após um atraso progressivo.
- DLQ é o destino final de mensagens que excederam o limite de tentativas e precisam de análise ou reprocessamento manual.

## Workers

O Docker Compose ainda executa um worker único para manter a operação simples nesta etapa. Internamente, o código já separa handlers por domínio:

- `audit_worker`
- `analytics_worker`
- `notification_worker`

Essa separação permite evoluir para processos independentes por fila sem reescrever a lógica de roteamento.

## Status de Evento

Os status foram centralizados em `backend/app/core/enums.py`:

- `received`
- `queued`
- `processing`
- `processed`
- `failed`
- `dead_letter`
- `publish_failed`

Os níveis de log também foram padronizados:

- `info`
- `warning`
- `error`

## Observabilidade

A pasta `backend/app/observability/` prepara a base para:

- logging estruturado;
- métricas Prometheus;
- geração e propagação de `correlation_id` e `trace_id`;
- integração futura com Grafana e Loki.

Nesta etapa, as métricas ainda são placeholders para manter a implementação simples e extensível.

## Estrutura

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
  nginx/
```

## Como Rodar com Docker Compose

1. Crie o arquivo de ambiente local:

```bash
cp .env.example .env
```

2. Suba os serviços:

```bash
docker compose up --build
```

3. Acesse:

- Aplicação via Nginx: http://localhost
- API direta: http://localhost:8000
- Health check: http://localhost/health
- RabbitMQ Management: http://localhost:15672

As credenciais padrão de desenvolvimento estão no `.env.example`. Elas não devem ser usadas em produção.

## Endpoints Iniciais

### `GET /health`

Retorna o status básico da API.

### `POST /api/events`

Cria, persiste e publica um evento na exchange `relay.events`.

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

### `GET /api/events`

Lista eventos recentes para alimentar o dashboard inicial.

## Desenvolvimento Local sem Docker

Use Python 3.12 para manter o ambiente local alinhado ao `backend/Dockerfile`.

Backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
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

Para execução sem Docker, ajuste `DATABASE_URL`, `RABBITMQ_*` e `REDIS_URL` conforme sua máquina.

## Roadmap Técnico

- Criar política de reprocessamento manual para eventos em DLQ.
- Separar workers por processo e fila.
- Adicionar autenticação e autorização.
- Adicionar filtros, paginação e detalhes de eventos no frontend.
- Expor métricas Prometheus no backend e nos workers.
- Adicionar logs estruturados com correlação por `correlation_id` e `trace_id`.
- Integrar Loki e Grafana.
- Criar pipeline de CI com lint, testes e build de imagens.
