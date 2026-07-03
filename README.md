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

Exchange:

- Nome: `relay.events`
- Tipo: `topic`
- Mensagens: persistentes

Filas iniciais:

- `relay.events.audit`
- `relay.events.analytics`
- `relay.events.notifications`
- `relay.events.dead_letter`

Bindings iniciais:

- `audit.*` e `events.*` -> `relay.events.audit`
- `analytics.*` -> `relay.events.analytics`
- `notifications.*` -> `relay.events.notifications`
- `dead_letter.*` -> `relay.events.dead_letter`

Exemplos de routing keys:

- `events.created`
- `audit.created`
- `analytics.page_viewed`
- `notifications.email_requested`

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

- Implementar retry com backoff.
- Criar DLQ real com dead-letter exchange e política de reprocessamento.
- Separar workers por processo e fila.
- Adicionar autenticação e autorização.
- Adicionar filtros, paginação e detalhes de eventos no frontend.
- Expor métricas Prometheus no backend e nos workers.
- Adicionar logs estruturados com correlação por `correlation_id` e `trace_id`.
- Integrar Loki e Grafana.
- Criar pipeline de CI com lint, testes e build de imagens.
