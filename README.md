# Relay

Relay é uma plataforma full stack de processamento assíncrono de eventos com FastAPI, RabbitMQ, PostgreSQL, Redis, Docker e React. O projeto evolui de uma fila simples para uma arquitetura baseada em exchange topic do RabbitMQ, com roteamento por domínio, status padronizados, `correlation_id` e `trace_id`.

## Stack

- Backend: Python, FastAPI, SQLAlchemy, Alembic e Pydantic
- Mensageria: RabbitMQ com exchange topic
- Cache: Redis
- Banco de dados: PostgreSQL
- Frontend: React, TypeScript e Vite
- Infra: Docker Compose e Nginx
- Observabilidade: Prometheus, Grafana, RabbitMQ Exporter, OpenTelemetry, Tempo, Loki e Alloy
- CI futura: GitHub Actions

## Arquitetura Atual

O backend recebe eventos via `POST /api/events`, persiste o registro no PostgreSQL e cria uma mensagem em `outbox_messages` na mesma transação. Um publisher separado lê a outbox e publica mensagens persistentes na exchange topic `relay.events`. A `routing_key` pode vir no payload da API; se não vier, o backend usa `events.created`.

Cada evento recebe:

- `correlation_id`: usado para correlacionar eventos de um mesmo fluxo de negócio. Pode vir da API ou ser gerado automaticamente.
- `trace_id`: usado para rastrear o ciclo de processamento. Pode vir da API ou ser gerado automaticamente.

Ambos são salvos no banco, registrados na outbox e enviados na mensagem publicada no RabbitMQ pelo publisher.

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

### Outbox Pattern

O Relay usa Outbox Pattern para garantir publicação confiável de eventos.

Fluxo final:

```text
API -> PostgreSQL events + outbox_messages -> relay-outbox-publisher -> RabbitMQ relay.events -> workers
```

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

- `OUTBOX_PUBLISHER_NAME=relay-outbox-publisher`
- `OUTBOX_BATCH_SIZE=10`
- `OUTBOX_POLL_INTERVAL_SECONDS=2`
- `OUTBOX_PUBLISHING_TIMEOUT_SECONDS=300`
- `OUTBOX_BACKOFF_SECONDS=5,30,120,300`
- `OUTBOX_METRICS_PORT=9101`

Serviço Docker Compose:

- `relay-outbox-publisher`

Métricas da outbox:

- `relay_outbox_messages_pending_total`: mensagens aguardando publicação.
- `relay_outbox_messages_failed_total`: mensagens aguardando nova tentativa após falha de publicação.
- `relay_outbox_messages_published_total`: mensagens publicadas com sucesso.
- `relay_outbox_publish_failures_total`: falhas de publicação.
- `relay_outbox_messages_stuck_publishing_total`: mensagens travadas em `publishing`.
- `relay_outbox_oldest_pending_age_seconds`: idade aproximada da mensagem pendente ou falhada mais antiga.
- `relay_outbox_retries_total`: retries agendados após falha de publicação.

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

### Idempotência dos Consumers

Os workers usam controle persistente de idempotência para evitar que o mesmo `event_id` execute o handler mais de uma vez com efeito colateral duplicado.

Tabela de controle:

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
3. Se o estado já for `processed` ou `dead_lettered`, a mensagem é considerada duplicada/terminal, um log é registrado e a mensagem original recebe `basic_ack`.
4. Se o estado for `processing` e ainda não estiver expirado, o worker não executa o handler, registra falha de lock/idempotência e faz `basic_ack`.
5. Se o estado for `pending`, `failed` ou `processing` expirado, o worker marca como `processing`, registra a tentativa e executa o handler dentro da transação.
6. Em sucesso, o estado vira `processed`.
7. Em erro, o estado vira `failed` e o fluxo existente decide retry ou DLQ.
8. Ao exceder retries, o estado vira `dead_lettered`.

Esse modelo protege contra:

- Mensagem duplicada no RabbitMQ.
- Retry de evento já processado.
- Dois workers tentando processar o mesmo `event_id`.
- Reentrega depois de timeout/conexão perdida.
- Worker crash antes do commit final.

Configuração:

- `IDEMPOTENCY_PROCESSING_TIMEOUT_SECONDS=900`

Métricas de idempotência:

- `relay_worker_events_duplicate_skipped_total`: mensagens ignoradas por evento já processado ou terminal.
- `relay_idempotency_lock_failures_total`: falhas ao adquirir processamento porque outro worker está ativo.
- `relay_idempotency_stale_processing_events_total`: eventos que permanecem em `processing` além do timeout configurado.
- `relay_worker_events_processed_total`: eventos processados com sucesso pelos workers.

Limitação importante: idempotência no consumer impede execução duplicada do handler dentro do Relay. Para efeitos colaterais externos, como envio de email ou chamada a API de terceiros, o destino externo também deve receber uma chave idempotente, normalmente o próprio `event_id`.

### DLQ Operacional

A DLQ operacional é a área usada para investigar mensagens que chegaram ao fim do ciclo automático de retry. O Relay mantém o histórico no PostgreSQL e expõe endpoints para listar, detalhar e reprocessar eventos mortos sem apagar registros.

Endpoints:

- `GET /api/dead-letter-events`: lista eventos em DLQ com motivo, erro, retry count, routing key original, `correlation_id`, `trace_id` e status do evento original.
- `GET /api/dead-letter-events/{id}`: retorna payload, evento original, tentativas e logs relacionados.
- `POST /api/dead-letter-events/{id}/reprocess`: republica o evento existente na exchange principal `relay.events`.

O reprocessamento manual usa `original_routing_key`, preserva `correlation_id` e `trace_id`, atualiza o evento original para `queued` e registra um `EventLog` com a ação operacional. O Relay não cria outro `Event` para essa operação.

Riscos de reprocessamento:

- O handler pode executar efeitos colaterais novamente se ainda não for idempotente.
- O erro original pode continuar acontecendo se a causa raiz não foi corrigida.
- O backend bloqueia reprocessamentos manuais repetidos em uma janela curta para reduzir cliques duplicados, mas isso não substitui idempotência real nos consumers.

Fluxo operacional recomendado:

1. Abrir a área de Dead Letter Queue no dashboard.
2. Verificar erro, payload, tentativas e logs.
3. Corrigir a causa raiz quando necessário.
4. Clicar em `Reprocessar`.
5. Acompanhar o evento voltar para `queued` e ser consumido pelo worker do domínio.

## Workers

O Docker Compose executa consumers independentes por domínio. Todos usam o mesmo código base de consumo resiliente, mas cada processo consome uma fila específica e aplica seu próprio conjunto de routing keys aceitas.

Workers:

- `relay-outbox-publisher`: lê `outbox_messages` e publica na exchange `relay.events`.
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

A API atua como producer transacional: recebe o evento, persiste no PostgreSQL e grava a mensagem na outbox. O `relay-outbox-publisher` publica na exchange `relay.events`. Os consumers são os workers: leem mensagens das filas, executam o handler de domínio, registram tentativas e aplicam retry ou DLQ quando necessário.

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

O Relay expõe métricas reais no endpoint:

- `GET /metrics`

O formato é compatível com Prometheus. O Docker Compose inclui um serviço `prometheus` configurado para coletar métricas do backend em `backend:8000/metrics`, do publisher em `relay-outbox-publisher:9101/metrics` e dos workers em `relay-*-worker:9102/metrics`.
O ambiente também inclui um RabbitMQ Exporter para coletar métricas reais do broker pela API de Management do RabbitMQ.

Para acessar:

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000
- Métricas diretas da API: http://localhost:8000/metrics
- RabbitMQ Exporter: http://localhost:9419/metrics
- Tempo: http://localhost:3200
- OpenTelemetry Collector OTLP gRPC: `localhost:4317`
- OpenTelemetry Collector OTLP HTTP: `localhost:4318`
- Loki: http://localhost:3100
- Alloy UI: http://localhost:12345

Credenciais locais do Grafana:

- Usuário: `relay`
- Senha: `relay_dev_password`

Essas credenciais são apenas para desenvolvimento local e podem ser alteradas via `GRAFANA_ADMIN_USER` e `GRAFANA_ADMIN_PASSWORD`.

Métricas principais:

- `relay_events_created_total`: eventos recebidos pela API.
- `relay_events_published_total`: eventos publicados no RabbitMQ.
- `relay_event_publish_failures_total`: falhas ao publicar no RabbitMQ.
- `relay_event_creation_duration_seconds`: tempo de criação do evento e registro na outbox.
- `relay_worker_events_processed_total`: eventos processados com sucesso por worker.
- `relay_worker_events_failed_total`: falhas de processamento por worker.
- `relay_worker_events_retried_total`: eventos enviados para retry.
- `relay_worker_events_dead_lettered_total`: eventos enviados para DLQ.
- `relay_worker_events_total`: transições de status registradas pelos workers.
- `relay_worker_events_duplicate_skipped_total`: mensagens ignoradas por idempotência.
- `relay_idempotency_lock_failures_total`: falhas ao adquirir lock de processamento.
- `relay_idempotency_stale_processing_events_total`: eventos presos em `processing` além do timeout.
- `relay_outbox_messages_pending_total`: mensagens na outbox aguardando publicação.
- `relay_outbox_messages_failed_total`: mensagens na outbox aguardando retry após falha.
- `relay_outbox_messages_published_total`: mensagens da outbox publicadas.
- `relay_outbox_publish_failures_total`: falhas de publicação da outbox.
- `relay_outbox_messages_stuck_publishing_total`: mensagens presas em `publishing`.
- `relay_outbox_oldest_pending_age_seconds`: idade da mensagem pendente ou falhada mais antiga.
- `relay_outbox_retries_total`: retries agendados pela outbox.
- `relay_event_processing_duration_seconds`: duração do processamento nos workers.
- `relay_dead_letter_events_total`: quantidade atual de eventos na DLQ.
- `relay_dead_letter_oldest_event_age_seconds`: idade aproximada do evento mais antigo na DLQ.
- `relay_dead_letter_reprocess_total`: reprocessamentos manuais solicitados.
- `relay_dead_letter_reprocess_failures_total`: falhas ou bloqueios de reprocessamento manual.
- `relay_events_by_status`: quantidade atual de eventos por status.
- `relay_event_attempts_by_status`: quantidade atual de tentativas por status.

Exemplos de PromQL:

```promql
sum(rate(relay_events_created_total[5m]))
sum(rate(relay_events_published_total[5m])) by (routing_key)
sum(rate(relay_worker_events_failed_total[5m])) by (worker_name, routing_key)
sum(rate(relay_worker_events_retried_total[5m])) by (retry_queue)
relay_dead_letter_events_total
relay_dead_letter_oldest_event_age_seconds
relay_outbox_messages_pending_total
relay_outbox_messages_failed_total
relay_outbox_messages_stuck_publishing_total
relay_outbox_oldest_pending_age_seconds
sum(rate(relay_outbox_publish_failures_total[5m])) by (routing_key)
sum(rate(relay_dead_letter_reprocess_total[15m])) by (routing_key)
histogram_quantile(0.95, sum(rate(relay_event_processing_duration_seconds_bucket[5m])) by (le, routing_key))
```

As métricas de API e reprocessamento manual são incrementadas diretamente no fluxo de código. As métricas atuais de status, tentativas e DLQ são calculadas a partir do PostgreSQL no momento do scrape. As métricas de worker são instrumentadas nos processos consumidores; para observabilidade completa por processo em produção, a próxima evolução é expor ou agregar métricas de cada worker individualmente.

### Grafana

O Grafana é provisionado automaticamente pelo Docker Compose.

Datasource:

- Nome: `Prometheus`
- URL interna: `http://prometheus:9090`
- Default: `true`

Dashboard versionado:

- Arquivo: `infra/grafana/dashboards/relay-observability.json`
- Nome no Grafana: `Relay Observability`
- Pasta: `Relay`

Painéis disponíveis:

- Eventos criados por minuto.
- Eventos publicados por routing key.
- Falhas de publicação.
- Eventos processados.
- Eventos com falha.
- Retries por fila.
- Eventos enviados para DLQ.
- Total atual de eventos em DLQ.
- Idade do evento mais antigo em DLQ.
- Reprocessamentos manuais.
- p95 de tempo de processamento.
- Status dos eventos.

Dashboard RabbitMQ:

- Arquivo: `infra/grafana/dashboards/relay-rabbitmq.json`
- Nome no Grafana: `Relay RabbitMQ`
- Pasta: `Relay`

Painéis do broker:

- Mensagens prontas por fila.
- Mensagens não confirmadas por fila.
- Consumidores por fila.
- Taxa de publicação.
- Taxa de entrega com ack.
- Mensagens em DLQ.
- Mensagens em retry queues.
- Estado das filas principais.
- Conexões.
- Canais.
- Memória usada por node.
- Mensagens totais por fila.

RabbitMQ Exporter:

- Serviço Docker Compose: `rabbitmq-exporter`
- Imagem: `kbudde/rabbitmq-exporter:1.0.0`
- URL interna do RabbitMQ: `http://rabbitmq:15672`
- Endpoint de métricas local: http://localhost:9419/metrics
- Job Prometheus: `rabbitmq-exporter`

Exemplos de PromQL para RabbitMQ:

```promql
rabbitmq_queue_messages_ready
rabbitmq_queue_messages_unacknowledged
rabbitmq_queue_consumers
sum(rate(rabbitmq_queue_messages_published_total[5m])) by (queue)
sum(rate(rabbitmq_queue_messages_delivered_total[5m])) by (queue)
rabbitmq_queue_messages{queue="relay.events.dead_letter"}
rabbitmq_queue_messages{queue=~"relay\\.events\\.retry\\..*"}
rabbitmq_queue_messages{queue=~"relay\\.events\\.(audit|analytics|notifications)"}
rabbitmq_queue_state{queue=~"relay\\.events\\.(audit|analytics|notifications)"}
rabbitmq_connections
rabbitmq_channels
rabbitmq_node_mem_used
```

Para verificar DLQ e retry queues pelo Grafana, abra o dashboard `Relay RabbitMQ` e acompanhe os painéis `Mensagens em DLQ`, `Mensagens em retry queues`, `Mensagens prontas por fila` e `Mensagens não confirmadas por fila`.

### Distributed Tracing

O Relay também possui tracing distribuído com OpenTelemetry, OpenTelemetry Collector e Grafana Tempo.

Fluxo local:

```text
FastAPI / Workers -> OTLP gRPC -> OpenTelemetry Collector -> Grafana Tempo -> Grafana
```

Serviços Docker Compose:

- `otel-collector`: recebe traces OTLP em `4317` gRPC e `4318` HTTP.
- `tempo`: armazena traces localmente e expõe consulta HTTP em `3200`.
- `grafana`: provisiona o datasource `Tempo` automaticamente.

Instrumentações oficiais usadas no backend:

- `opentelemetry-instrumentation-fastapi`: gera spans para requisições HTTP da API.
- `opentelemetry-instrumentation-sqlalchemy`: gera spans para operações no PostgreSQL via SQLAlchemy.
- `opentelemetry-instrumentation-pika`: instrumenta publicação e consumo via RabbitMQ quando suportado pela biblioteca.
- `opentelemetry-exporter-otlp-proto-grpc`: exporta spans para o Collector via OTLP gRPC.

Variáveis principais:

- `OTEL_ENABLED=true`
- `OTEL_SERVICE_NAME=relay-backend`
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317`
- `OTEL_EXPORTER_OTLP_INSECURE=true`
- `OTEL_TRACES_SAMPLER=always_on`
- `OTEL_RESOURCE_ATTRIBUTES=service.namespace=relay,deployment.environment=development`

Os workers usam `OTEL_SERVICE_NAME` próprio no Docker Compose:

- `relay-audit-worker`
- `relay-analytics-worker`
- `relay-notification-worker`

O `trace_id` de negócio existente é preservado. Quando um evento novo chega sem `trace_id`, o backend usa o trace id OpenTelemetry atual, se houver contexto ativo; caso contrário, gera um UUID como fallback. O `correlation_id` continua independente e não foi removido.

Datasource Tempo:

- Nome: `Tempo`
- UID: `tempo`
- URL interna: `http://tempo:3200`

Dashboard de tracing:

- Arquivo: `infra/grafana/dashboards/relay-tracing.json`
- Nome no Grafana: `Relay Tracing`
- Pasta: `Relay`

Painéis disponíveis:

- Quantidade de traces.
- Latência média.
- P95 e P99.
- Erros por endpoint.
- Tempo gasto por operação.
- Traces recentes.

Exemplos de TraceQL Metrics:

```traceql
{ resource.service.namespace = "relay" } | rate()
{ resource.service.namespace = "relay" } | avg_over_time(duration)
{ resource.service.namespace = "relay" } | quantile_over_time(duration, .95)
{ resource.service.namespace = "relay" } | quantile_over_time(duration, .99)
{ resource.service.name = "relay-backend" && status = error } | rate() by (span.http.route)
{ resource.service.namespace = "relay" } | avg_over_time(duration) by (span:name)
```

Validação da configuração:

```bash
docker compose run --rm --entrypoint /otelcol-contrib otel-collector validate --config=/etc/otelcol-contrib/config.yml
docker compose run --rm --entrypoint /tempo tempo --config.file=/etc/tempo/tempo.yml --config.verify=true
```

Limitações atuais:

- O ambiente usa armazenamento local do Tempo, adequado para desenvolvimento.
- Não há sampling adaptativo nem tail sampling nesta etapa.
- O dashboard usa TraceQL Metrics; ele depende de traces reais ingeridos no Tempo para exibir dados.

### Logs Centralizados

O Relay possui uma stack local de logs centralizados com Grafana Loki e Grafana Alloy.

Fluxo local:

```text
Containers Docker -> Grafana Alloy -> Grafana Loki -> Grafana
```

Serviços Docker Compose:

- `loki`: armazena logs localmente e expõe API em `3100`.
- `alloy`: descobre containers Docker pelo socket `/var/run/docker.sock`, coleta stdout/stderr e envia para Loki.
- `grafana`: provisiona o datasource `Loki` automaticamente.

Serviços coletados:

- `backend`
- `relay-audit-worker`
- `relay-analytics-worker`
- `relay-notification-worker`
- `postgres`
- `rabbitmq`
- `redis`
- `frontend`
- `nginx`
- `prometheus`
- `rabbitmq-exporter`
- `tempo`
- `otel-collector`
- `grafana`
- `loki`
- `alloy`

Logs estruturados do backend:

O backend escreve logs JSON em stdout. Os campos principais são:

- `timestamp`
- `level`
- `service`
- `environment`
- `trace_id`
- `span_id`
- `correlation_id`
- `event_id`
- `endpoint`
- `logger`
- `message`

O `trace_id` e o `span_id` são extraídos do contexto OpenTelemetry ativo. O `correlation_id`, `event_id` e `endpoint` aparecem quando o fluxo possui esses dados.

Datasource Loki:

- Nome: `Loki`
- UID: `loki`
- URL interna: `http://loki:3100`
- Derived field: `TraceID`
- Integração: ao encontrar `trace_id` no log, o Grafana cria um link para abrir o trace correspondente no datasource `Tempo`.

Dashboard de logs:

- Arquivo: `infra/grafana/dashboards/relay-logs.json`
- Nome no Grafana: `Relay Logs`
- Pasta: `Relay`

Painéis disponíveis:

- Volume de logs por serviço.
- Erros por serviço.
- Logs recentes.
- Logs filtráveis por `trace_id`.
- Logs filtráveis por `correlation_id`.
- Logs do worker por `event_id`.
- Logs relacionados a DLQ, retry e falhas de publicação.

Exemplos de LogQL:

```logql
{job="relay/docker"}
{job="relay/docker", service="backend"} | json
{job="relay/docker"} | json | trace_id="TRACE_ID"
{job="relay/docker"} | json | correlation_id="CORRELATION_ID"
{job="relay/docker", service=~"relay-.*-worker"} | json | event_id="EVENT_ID"
{job="relay/docker"} |~ "(?i)(dlq|dead letter|retry|publish failed|failed to publish)"
sum by (service) (count_over_time({job="relay/docker"}[5m]))
sum by (service) (count_over_time({job="relay/docker", level=~"error|critical"}[5m]))
```

Validação da configuração:

```bash
docker compose run --rm --entrypoint /usr/bin/loki loki --config.file=/etc/loki/loki.yml --verify-config=true
docker compose run --rm --entrypoint /bin/alloy alloy validate /etc/alloy/config.alloy
```

Limitações atuais:

- Loki usa armazenamento local, adequado para desenvolvimento.
- `trace_id`, `correlation_id` e `event_id` ficam no JSON do log, não como labels, para evitar alta cardinalidade.
- Logs de serviços de terceiros podem não ser JSON; ainda assim são coletados por container e serviço.

### Alertas Operacionais

O Prometheus carrega regras versionadas a partir de `infra/prometheus/rules/relay-alerts.yml` e envia alertas para o Alertmanager. O Alertmanager possui configuração versionada com rotas por severidade e receivers webhook genéricos.

Arquivos de configuração:

- Regras Prometheus: `infra/prometheus/rules/relay-alerts.yml`
- Configuração Prometheus: `infra/prometheus/prometheus.yml`
- Configuração Alertmanager: `infra/alertmanager/alertmanager.yml`

Serviços:

- Prometheus: http://localhost:9090
- Alertmanager: http://localhost:9093
- Grafana: http://localhost:3000

Datasource Grafana:

- Nome: `Alertmanager`
- UID: `alertmanager`
- URL interna: `http://alertmanager:9093`
- Implementação: `prometheus`

Dashboard de alertas:

- Arquivo: `infra/grafana/dashboards/relay-alerts.json`
- Nome no Grafana: `Relay Alerts`
- Pasta: `Relay`

Dashboard operacional de Outbox e Idempotência:

- Arquivo: `infra/grafana/dashboards/relay-outbox-idempotency.json`
- Nome no Grafana: `Relay Outbox & Idempotency`
- Painéis: pending outbox, failed outbox, published outbox, retries, mensagens presas em `publishing`, duplicidades ignoradas, falhas de lock, eventos presos em `processing` e saúde do `relay-outbox-publisher`.

Alertas de aplicação:

- `RelayDeadLetterQueueHasEvents` (`warning`): existe pelo menos um evento em DLQ por mais de 5 minutos.
- `RelayDeadLetterQueueGrowing` (`critical`): a quantidade de eventos em DLQ aumentou nos últimos 10 minutos.
- `RelayOldDeadLetterEvent` (`warning`): o evento mais antigo na DLQ passou de 30 minutos.
- `RelayHighRetryRate` (`warning`): workers estão enviando muitos eventos para retry.
- `RelayHighWorkerFailureRate` (`warning`): workers estão falhando eventos em taxa elevada.
- `RelayEventPublishFailures` (`critical`): houve falha ao publicar eventos no RabbitMQ.

Alertas de RabbitMQ e scrape:

- `RelayQueueWithoutConsumers` (`critical`): fila principal sem consumidores.
- `RelayQueueBacklogGrowing` (`warning`): mensagens prontas crescendo em filas principais.
- `RelayHighUnackedMessages` (`warning`): mensagens não confirmadas acima do limite.
- `RelayRetryQueueBacklog` (`warning`): filas de retry acumulando mensagens.
- `RelayRabbitMQExporterDown` (`critical`): Prometheus não consegue coletar o RabbitMQ Exporter.
- `RelayBackendMetricsDown` (`critical`): Prometheus não consegue coletar `/metrics` do backend.

Alertas de Outbox:

- `RelayOutboxPendingTooLong` (`warning`): a mensagem pendente ou falhada mais antiga ficou tempo demais sem publicação.
- `RelayOutboxPublishFailures` (`critical`): houve falha real de publicação da outbox no RabbitMQ.
- `RelayOutboxHighRetryRate` (`warning`): o publisher está reagendando retries em taxa elevada.
- `RelayOutboxStuckPublishing` (`critical`): existe mensagem presa em `publishing` além do timeout configurado.
- `RelayOutboxPublisherDown` (`critical`): Prometheus não consegue coletar o endpoint `/metrics` do `relay-outbox-publisher`.

Alertas de Idempotência:

- `RelayDuplicateEventsSkipped` (`info`): workers ignoraram mensagens duplicadas ou já terminais.
- `RelayIdempotencyLockFailures` (`warning`): workers não conseguiram adquirir o lock de processamento.
- `RelayStaleProcessingEvents` (`critical`): existe evento preso em `processing` além de `IDEMPOTENCY_PROCESSING_TIMEOUT_SECONDS`.

Métricas usadas:

- Gauges do Relay: `relay_dead_letter_events_total`, `relay_dead_letter_oldest_event_age_seconds`.
- Gauges de Outbox e Idempotência: `relay_outbox_messages_pending_total`, `relay_outbox_messages_failed_total`, `relay_outbox_messages_stuck_publishing_total`, `relay_outbox_oldest_pending_age_seconds`, `relay_idempotency_stale_processing_events_total`.
- Counters do Relay: `relay_worker_events_retried_total`, `relay_worker_events_failed_total`, `relay_event_publish_failures_total`, `relay_outbox_publish_failures_total`, `relay_outbox_retries_total`, `relay_worker_events_duplicate_skipped_total`, `relay_idempotency_lock_failures_total`.
- Gauges do RabbitMQ Exporter: `rabbitmq_queue_consumers`, `rabbitmq_queue_messages_ready`, `rabbitmq_queue_messages_unacknowledged`, `rabbitmq_queue_messages`.
- Métrica nativa do Prometheus: `up`.

As regras usam `rate` e `increase` apenas para counters. Para gauges, como contadores atuais de filas, idade e estados presos, as regras usam `max_over_time` e `min_over_time`.

Investigação de incidentes de Outbox:

1. Abrir o dashboard `Relay Outbox & Idempotency` e verificar `Outbox publisher health`, `Outbox pending`, `Outbox failed`, `Stuck publishing` e `Oldest pending outbox age`.
2. Consultar os logs no dashboard `Relay Logs` filtrando `service="relay-outbox-publisher"` e, se disponível, `outbox_message_id`, `event_id`, `correlation_id` ou `trace_id`.
3. Conferir a tabela `outbox_messages` para `status`, `attempt_count`, `last_error`, `last_attempt_at`, `next_attempt_at`, `locked_by` e `locked_at`.
4. Se o alerta for de publicação, verificar RabbitMQ, exchange `relay.events`, conectividade e credenciais.
5. Se o alerta for de `publishing` travado, verificar se o publisher caiu durante publicação e se a recuperação automática marcou a mensagem como `failed` para retry.

Investigação de incidentes de Idempotência:

1. Abrir o dashboard `Relay Outbox & Idempotency` e verificar `Duplicate skipped rate`, `Idempotency lock failures rate` e `Stale processing events`.
2. Consultar logs dos workers filtrando `event_id`, `correlation_id` ou `trace_id`.
3. Conferir `event_processing_states` para `status`, `processing_started_at`, `worker_name`, `attempt_count` e timestamps.
4. Para duplicidades, confirmar se houve redelivery do RabbitMQ, retry manual ou publicação duplicada pela outbox após crash.
5. Para eventos presos em `processing`, validar timeout, estado do worker responsável e possíveis falhas parciais no handler.

Validação das regras:

```bash
docker compose run --rm --entrypoint promtool prometheus check rules /etc/prometheus/rules/relay-alerts.yml
```

Validação do Alertmanager:

```bash
docker compose run --rm --entrypoint amtool alertmanager check-config /etc/alertmanager/alertmanager.yml
```

Rotas de notificação:

- `severity="critical"` -> receiver `webhook-critical`
- `severity="warning"` -> receiver `webhook-warning`
- `severity="info"` -> receiver `webhook-info`

Receivers webhook:

- `webhook-critical`: `http://host.docker.internal:8080/alerts/critical`
- `webhook-warning`: `http://host.docker.internal:8080/alerts/warning`
- `webhook-info`: `http://host.docker.internal:8080/alerts/info`

Esses endpoints são genéricos e próprios para desenvolvimento local. Não há secrets reais no repositório.

Também é possível acompanhar os alertas em:

- Prometheus: http://localhost:9090/alerts
- Alertmanager: http://localhost:9093
- Grafana: dashboard `Relay Alerts`

Limitação atual: o Prometheus coleta o endpoint `/metrics` do backend, do `relay-outbox-publisher` e dos workers. Como cada worker expõe métricas no próprio processo, escalar vários containers do mesmo serviço exige atenção aos targets efetivos do Prometheus. As métricas derivadas do PostgreSQL, como status, DLQ, outbox e idempotência, aparecem pelo scrape do backend.

Próximos passos de observabilidade:

- Avaliar service discovery mais robusto para múltiplas réplicas de workers.
- Conectar receivers reais como Slack, email, Discord, PagerDuty ou webhook corporativo.
- Versionar dashboards adicionais para RabbitMQ e infraestrutura.

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
- RabbitMQ Exporter: http://localhost:9419/metrics
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000

As credenciais padrão de desenvolvimento estão no `.env.example`. Elas não devem ser usadas em produção.

## Endpoints Iniciais

### `GET /health`

Retorna o status básico da API.

### `POST /api/events`

Cria e persiste um evento, registrando a mensagem correspondente em `outbox_messages`. A publicação na exchange `relay.events` é feita pelo `relay-outbox-publisher`.

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

### `GET /api/dead-letter-events`

Lista eventos que foram enviados para DLQ.

### `GET /api/dead-letter-events/{id}`

Mostra detalhes operacionais do evento morto, incluindo payload, tentativas e logs.

### `POST /api/dead-letter-events/{id}/reprocess`

Republica o evento original na exchange principal usando a routing key original.

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

- Adicionar estado operacional para DLQ resolvida ou descartada.
- Adicionar autenticação e autorização.
- Adicionar filtros, paginação e detalhes de eventos no frontend.
- Expor métricas Prometheus no backend e nos workers.
- Conectar receivers reais do Alertmanager a Slack, email, Discord, PagerDuty ou webhook corporativo.
- Criar alertas baseados em logs do Loki para erros críticos e falhas operacionais.
- Criar pipeline de CI com lint, testes e build de imagens.
