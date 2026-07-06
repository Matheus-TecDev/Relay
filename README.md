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
- `retry.*` -> filas de domínio para liberação de mensagens após TTL

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

Quando o TTL expira, a mensagem volta para a exchange principal `relay.events`. O header `x-original-routing-key` preserva a routing key original do evento para que o worker correto resolva o handler mesmo quando a mensagem retorna a partir de uma fila de retry.

Diferença entre retry e DLQ:

- Retry é uma tentativa controlada de reprocessar uma falha recuperável após um atraso progressivo.
- DLQ é o destino final de mensagens que excederam o limite de tentativas e precisam de análise ou reprocessamento manual.

## Workers

O Docker Compose executa consumers independentes por domínio. Todos usam o mesmo código base de consumo resiliente, mas cada processo consome uma fila específica e aplica seu próprio conjunto de routing keys aceitas.

Workers:

- `relay-audit-worker`: consome `relay.events.audit` e processa `events.*` e `audit.*`.
- `relay-analytics-worker`: consome `relay.events.analytics` e processa `analytics.*`.
- `relay-notification-worker`: consome `relay.events.notifications` e processa `notifications.*`.

Entry points:

- `python -m app.workers.audit_consumer`
- `python -m app.workers.analytics_consumer`
- `python -m app.workers.notification_consumer`

Cada serviço recebe:

- `WORKER_NAME`: nome lógico do worker.
- `WORKER_QUEUE`: fila consumida pelo processo.
- `WORKER_ROUTING_KEY`: padrões de routing key aceitos pelo worker.

Os handlers continuam separados por domínio:

- `audit_worker`
- `analytics_worker`
- `notification_worker`

A API atua como producer: recebe o evento, persiste no PostgreSQL e publica na exchange `relay.events`. Os consumers são os workers: leem mensagens das filas, executam o handler de domínio, registram tentativas e aplicam retry ou DLQ quando necessário.

Para escalar um domínio específico com Docker Compose:

```bash
docker compose up --scale relay-analytics-worker=3
```

Também é possível escalar audit e notification separadamente:

```bash
docker compose up --scale relay-audit-worker=2 --scale relay-notification-worker=2
```

As mensagens liberadas pelas filas de retry usam `retry.*` para voltar à exchange principal. Como as filas de retry são compartilhadas, os workers verificam `x-original-routing-key` antes de processar; apenas o worker compatível com a routing key original executa o handler.

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
- Adicionar autenticação e autorização.
- Adicionar filtros, paginação e detalhes de eventos no frontend.
- Expor métricas Prometheus no backend e nos workers.
- Adicionar logs estruturados com correlação por `correlation_id` e `trace_id`.
- Integrar Loki e Grafana.
- Criar pipeline de CI com lint, testes e build de imagens.
