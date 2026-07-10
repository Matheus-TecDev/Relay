# Arquitetura

O Relay é uma plataforma full stack de processamento assíncrono de eventos. A API recebe eventos, persiste o estado no PostgreSQL e registra uma mensagem em `outbox_messages` na mesma transação. Um publisher separado publica mensagens persistentes na exchange topic `relay.events`, e workers independentes consomem filas por domínio.

## Fluxo Completo

```text
Client
  -> FastAPI /api/events
  -> PostgreSQL events + event_processing_states + outbox_messages
  -> relay-outbox-publisher
  -> RabbitMQ relay.events
  -> relay.events.audit | relay.events.analytics | relay.events.notifications
  -> domain workers
  -> retry queues or relay.events.dead_letter
  -> PostgreSQL attempts, logs and DLQ records
  -> Prometheus, Grafana, Loki, Tempo and Alertmanager
```

![Relay Architecture](../assets/architecture.png)

Cada evento recebe:

- `correlation_id`: correlaciona eventos de um mesmo fluxo de negócio. Pode vir da API ou ser gerado automaticamente.
- `trace_id`: rastreia o ciclo de processamento. Pode vir da API, do contexto OpenTelemetry ativo ou ser gerado automaticamente.

Ambos são salvos no banco, registrados na outbox e enviados para o RabbitMQ.

## Componentes

| Componente | Responsabilidade |
| --- | --- |
| FastAPI backend | Recebe eventos, consulta estado operacional e expõe métricas |
| PostgreSQL | Persiste eventos, outbox, tentativas, logs, DLQ e estados de idempotência |
| `relay-outbox-publisher` | Publica mensagens da outbox na exchange principal |
| RabbitMQ | Roteia eventos por domínio com exchange topic, DLX e filas de retry |
| Workers | Processam mensagens por domínio e aplicam retry, DLQ e idempotência |
| Redis | Cache/infra auxiliar da aplicação |
| React frontend | Exibe eventos e operação da DLQ |
| Observability stack | Prometheus, Grafana, Loki, Tempo, OpenTelemetry, Alloy e Alertmanager |

## Configuração Principal

Variáveis de ambiente relevantes para a arquitetura local:

```env
PROJECT_NAME=Relay
ENVIRONMENT=development
API_V1_PREFIX=/api
DATABASE_URL=postgresql+psycopg://relay:relay_dev_password@postgres:5432/relay
RABBITMQ_DEFAULT_USER=relay
RABBITMQ_DEFAULT_PASS=relay_dev_password
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672
RABBITMQ_USER=relay
RABBITMQ_PASSWORD=relay_dev_password
RABBITMQ_VHOST=/
RABBITMQ_EXCHANGE=relay.events
RABBITMQ_DLX=relay.events.dlx
RABBITMQ_AUDIT_QUEUE=relay.events.audit
RABBITMQ_ANALYTICS_QUEUE=relay.events.analytics
RABBITMQ_NOTIFICATIONS_QUEUE=relay.events.notifications
RABBITMQ_DEAD_LETTER_QUEUE=relay.events.dead_letter
RABBITMQ_RETRY_QUEUE_10S=relay.events.retry.10s
RABBITMQ_RETRY_QUEUE_30S=relay.events.retry.30s
RABBITMQ_RETRY_QUEUE_5M=relay.events.retry.5m
RABBITMQ_MAX_RETRIES=3
REDIS_URL=redis://redis:6379/0
BACKEND_CORS_ORIGINS=http://localhost,http://localhost:5173,http://127.0.0.1:5173
```

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

## Outbox Pattern

O Relay usa Transactional Outbox para garantir que a criação do evento e o registro da mensagem a ser publicada aconteçam na mesma transação de banco. A API não publica diretamente no RabbitMQ.

```text
API -> PostgreSQL events + outbox_messages -> relay-outbox-publisher -> RabbitMQ relay.events -> workers
```

Na criação de um evento, o backend persiste:

- `events`
- `event_processing_states`
- `outbox_messages`

O `relay-outbox-publisher` lê mensagens pendentes, adquire lock transacional, publica na exchange `relay.events` e atualiza o status da outbox.

Tabela:

- `outbox_messages`

Estados:

- `pending`: mensagem registrada no banco e aguardando publicação.
- `publishing`: publisher adquiriu lock e está tentando publicar.
- `published`: mensagem publicada no RabbitMQ.
- `failed`: tentativa de publicação falhou e a mensagem ficou agendada para retry.

Garantias:

- O `Event`, o estado inicial de idempotência e a `outbox_messages` são criados na mesma transação.
- A API não publica diretamente no RabbitMQ.
- O publisher usa lock transacional com `SELECT ... FOR UPDATE SKIP LOCKED` quando suportado pelo banco.
- Se dois publishers rodarem ao mesmo tempo, apenas um deve adquirir a mensagem.
- Se o RabbitMQ estiver indisponível, a mensagem fica `failed`, com `attempt_count`, `last_error`, `last_attempt_at` e `next_attempt_at`.
- Se o publisher cair durante `publishing`, mensagens antigas voltam para retry após `OUTBOX_PUBLISHING_TIMEOUT_SECONDS`.
- Se a publicação for duplicada por crash entre RabbitMQ e commit no banco, a idempotência dos consumers protege o handler contra efeito colateral duplicado dentro do Relay.

Configuração:

```env
OUTBOX_PUBLISHER_NAME=relay-outbox-publisher
OUTBOX_BATCH_SIZE=10
OUTBOX_POLL_INTERVAL_SECONDS=2
OUTBOX_PUBLISHING_TIMEOUT_SECONDS=300
OUTBOX_BACKOFF_SECONDS=5,30,120,300
OUTBOX_METRICS_PORT=9101
```

Serviço Docker Compose:

- `relay-outbox-publisher`

Riscos e limitações:

- A outbox fornece publicação confiável, mas não elimina totalmente publicação duplicada em caso de crash depois da publicação e antes do commit do status `published`.
- A proteção contra duplicidade depende dos consumers idempotentes.
- Efeitos colaterais externos ainda precisam receber uma chave idempotente, normalmente `event_id`.
- Múltiplas réplicas do publisher exigem observabilidade cuidadosa dos locks, mensagens presas e retries.

## Workers

O Docker Compose executa consumers independentes por domínio. Todos usam o mesmo código base de consumo resiliente, mas cada processo consome uma fila específica e aplica seu próprio conjunto de routing keys aceitas.

| Serviço | Fila | Routing keys | Entry point |
| --- | --- | --- | --- |
| `relay-outbox-publisher` | `outbox_messages` | N/A | `python -m app.workers.outbox_publisher` |
| `relay-audit-worker` | `relay.events.audit` | `events.*`, `audit.*` | `python -m app.workers.audit_consumer` |
| `relay-analytics-worker` | `relay.events.analytics` | `analytics.*` | `python -m app.workers.analytics_consumer` |
| `relay-notification-worker` | `relay.events.notifications` | `notifications.*` | `python -m app.workers.notification_consumer` |

Variáveis por worker:

- `WORKER_NAME`: nome lógico do worker.
- `WORKER_QUEUE`: fila consumida pelo processo.
- `WORKER_ROUTING_KEY`: padrões de routing key aceitos pelo worker.

Os handlers continuam separados por domínio:

- `audit_worker`
- `analytics_worker`
- `notification_worker`

Para escalar um domínio específico com Docker Compose:

```bash
docker compose up --scale relay-analytics-worker=3
docker compose up --scale relay-audit-worker=2 --scale relay-notification-worker=2
```

As mensagens liberadas pelas filas de retry usam `retry.*` para voltar à exchange principal. Como as filas de retry são compartilhadas, os workers verificam `x-original-routing-key` antes de processar; apenas o worker compatível com a routing key original executa o handler.

## Retry e Backoff

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

Retry é uma tentativa controlada de reprocessar uma falha recuperável após um atraso progressivo. DLQ é o destino final de mensagens que excederam o limite de tentativas e precisam de análise ou reprocessamento manual.

## Dead Letter Queue

A DLQ operacional é a área usada para investigar mensagens que chegaram ao fim do ciclo automático de retry. O Relay mantém o histórico no PostgreSQL e expõe endpoints para listar, detalhar e reprocessar eventos mortos sem apagar registros.

Endpoints:

- `GET /api/dead-letter-events`: lista eventos em DLQ com motivo, erro, retry count, routing key original, `correlation_id`, `trace_id` e status do evento original.
- `GET /api/dead-letter-events/{id}`: retorna payload, evento original, tentativas e logs relacionados.
- `POST /api/dead-letter-events/{id}/reprocess`: republica o evento existente na exchange principal `relay.events`.

O reprocessamento manual usa `original_routing_key`, preserva `correlation_id` e `trace_id`, atualiza o evento original para `queued` e registra um `EventLog` com a ação operacional. O Relay não cria outro `Event` para essa operação.

Fluxo operacional recomendado:

1. Abrir a área de Dead Letter Queue no dashboard.
2. Verificar erro, payload, tentativas e logs.
3. Corrigir a causa raiz quando necessário.
4. Clicar em `Reprocessar`.
5. Acompanhar o evento voltar para `queued` e ser consumido pelo worker do domínio.

Riscos de reprocessamento:

- O handler pode executar efeitos colaterais novamente se ainda não for idempotente.
- O erro original pode continuar acontecendo se a causa raiz não foi corrigida.
- O backend bloqueia reprocessamentos manuais repetidos em uma janela curta para reduzir cliques duplicados, mas isso não substitui idempotência real nos consumers.

## Idempotência

Os workers usam controle persistente de idempotência para evitar que o mesmo `event_id` execute o handler mais de uma vez com efeito colateral duplicado dentro do Relay.

Tabela:

- `event_processing_states`

Estados persistidos:

- `pending`: evento criado e ainda não processado.
- `processing`: worker adquiriu o lock e está executando o handler.
- `processed`: handler concluiu com sucesso; mensagens duplicadas são ignoradas.
- `failed`: handler falhou antes de concluir; retries podem tentar novamente.
- `dead_lettered`: evento excedeu o limite de retry e foi enviado para DLQ.

Estratégia:

1. A API cria o `Event` e também cria `event_processing_states` com status `pending`.
2. Ao consumir uma mensagem, o worker abre uma transação e bloqueia a linha de idempotência com `SELECT ... FOR UPDATE`.
3. Se o estado já for `processed` ou `dead_lettered`, a mensagem é considerada duplicada ou terminal, um log é registrado e a mensagem original recebe `basic_ack`.
4. Se o estado for `processing` e ainda não estiver expirado, o worker não executa o handler, registra falha de lock/idempotência e faz `basic_ack`.
5. Se o estado for `pending`, `failed` ou `processing` expirado, o worker marca como `processing`, registra a tentativa e executa o handler dentro da transação.
6. Em sucesso, o estado vira `processed`.
7. Em erro, o estado vira `failed` e o fluxo decide retry ou DLQ.
8. Ao exceder retries, o estado vira `dead_lettered`.

Configuração:

```env
IDEMPOTENCY_PROCESSING_TIMEOUT_SECONDS=900
```

Esse modelo protege contra:

- Mensagem duplicada no RabbitMQ.
- Retry de evento já processado.
- Dois workers tentando processar o mesmo `event_id`.
- Reentrega depois de timeout ou conexão perdida.
- Worker crash antes do commit final.
- Publicação duplicada pela outbox após crash entre RabbitMQ e commit no banco.

Limitação importante: idempotência no consumer impede execução duplicada do handler dentro do Relay. Para efeitos colaterais externos, como envio de email ou chamada a API de terceiros, o destino externo também deve receber uma chave idempotente, normalmente o próprio `event_id`.

## Status

Status de evento centralizados em `backend/app/core/enums.py`:

- `received`
- `queued`
- `processing`
- `processed`
- `failed`
- `dead_letter`
- `publish_failed`

Níveis de log padronizados:

- `info`
- `warning`
- `error`

## Estrutura Interna

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

Em Linux/macOS, a ativação do virtualenv normalmente usa:

```bash
source .venv/bin/activate
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Para execução sem Docker, ajuste `DATABASE_URL`, `RABBITMQ_*` e `REDIS_URL` conforme sua máquina.
