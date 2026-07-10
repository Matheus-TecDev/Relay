# Observabilidade

O Relay inclui uma stack local para observar API, publisher, workers, RabbitMQ e infraestrutura de apoio.

## Stack

- Prometheus: coleta métricas da API, do `relay-outbox-publisher`, dos workers e do RabbitMQ Exporter.
- Grafana: dashboards versionados para aplicação, RabbitMQ, tracing, logs, alertas, outbox e idempotência.
- RabbitMQ Exporter: coleta métricas reais do broker pela API de Management do RabbitMQ.
- Loki: armazena logs localmente.
- Alloy: descobre containers Docker, coleta stdout/stderr e envia logs para Loki.
- OpenTelemetry Collector: recebe traces OTLP.
- Tempo: armazena traces localmente e permite consulta pelo Grafana.
- Alertmanager: recebe alertas do Prometheus e roteia por severidade.

## URLs Locais

- Prometheus: http://localhost:9090
- Alertmanager: http://localhost:9093
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

## Prometheus

O Relay expõe métricas reais no endpoint:

- `GET /metrics`

O Docker Compose inclui um serviço `prometheus` configurado para coletar métricas do backend em `backend:8000/metrics`, do publisher em `relay-outbox-publisher:9101/metrics` e dos workers em `relay-*-worker:9102/metrics`.

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

As métricas de API e reprocessamento manual são incrementadas diretamente no fluxo de código. As métricas atuais de status, tentativas e DLQ são calculadas a partir do PostgreSQL no momento do scrape. As métricas de worker são instrumentadas nos processos consumidores.

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

## Grafana

Datasource Prometheus:

- Nome: `Prometheus`
- URL interna: `http://prometheus:9090`
- Default: `true`

Dashboards versionados:

- `infra/grafana/dashboards/relay-observability.json`: `Relay Observability`
- `infra/grafana/dashboards/relay-rabbitmq.json`: `Relay RabbitMQ`
- `infra/grafana/dashboards/relay-tracing.json`: `Relay Tracing`
- `infra/grafana/dashboards/relay-logs.json`: `Relay Logs`
- `infra/grafana/dashboards/relay-alerts.json`: `Relay Alerts`
- `infra/grafana/dashboards/relay-outbox-idempotency.json`: `Relay Outbox & Idempotency`

Painéis do dashboard `Relay Observability`:

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

Painéis do dashboard `Relay RabbitMQ`:

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

Para verificar DLQ e retry queues pelo Grafana, abra o dashboard `Relay RabbitMQ` e acompanhe os painéis `Mensagens em DLQ`, `Mensagens em retry queues`, `Mensagens prontas por fila` e `Mensagens não confirmadas por fila`.

## RabbitMQ Exporter

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

## Tracing

O Relay possui tracing distribuído com OpenTelemetry, OpenTelemetry Collector e Grafana Tempo.

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

```env
OTEL_ENABLED=true
OTEL_SERVICE_NAME=relay-backend
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_INSECURE=true
OTEL_TRACES_SAMPLER=always_on
OTEL_RESOURCE_ATTRIBUTES=service.namespace=relay,deployment.environment=development
```

Os workers usam `OTEL_SERVICE_NAME` próprio no Docker Compose:

- `relay-audit-worker`
- `relay-analytics-worker`
- `relay-notification-worker`

O `trace_id` de negócio existente é preservado. Quando um evento novo chega sem `trace_id`, o backend usa o trace id OpenTelemetry atual, se houver contexto ativo; caso contrário, gera um UUID como fallback. O `correlation_id` continua independente.

Datasource Tempo:

- Nome: `Tempo`
- UID: `tempo`
- URL interna: `http://tempo:3200`

Painéis do dashboard `Relay Tracing`:

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

Limitações atuais:

- O ambiente usa armazenamento local do Tempo, adequado para desenvolvimento.
- Não há sampling adaptativo nem tail sampling nesta etapa.
- O dashboard usa TraceQL Metrics; ele depende de traces reais ingeridos no Tempo para exibir dados.

## Logs

O Relay possui uma stack local de logs centralizados com Grafana Loki e Grafana Alloy.

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

Painéis do dashboard `Relay Logs`:

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

Limitações atuais:

- Loki usa armazenamento local, adequado para desenvolvimento.
- `trace_id`, `correlation_id` e `event_id` ficam no JSON do log, não como labels, para evitar alta cardinalidade.
- Logs de serviços de terceiros podem não ser JSON; ainda assim são coletados por container e serviço.

## Alertas

O Prometheus carrega regras versionadas a partir de `infra/prometheus/rules/relay-alerts.yml` e envia alertas para o Alertmanager. O Alertmanager possui configuração versionada com rotas por severidade e receivers webhook genéricos.

Arquivos de configuração:

- Regras Prometheus: `infra/prometheus/rules/relay-alerts.yml`
- Configuração Prometheus: `infra/prometheus/prometheus.yml`
- Configuração Alertmanager: `infra/alertmanager/alertmanager.yml`

Datasource Grafana:

- Nome: `Alertmanager`
- UID: `alertmanager`
- URL interna: `http://alertmanager:9093`
- Implementação: `prometheus`

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

Alertas de idempotência:

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
- Criar alertas baseados em logs do Loki para erros críticos e falhas operacionais.

## Validações

Validar regras Prometheus:

```bash
docker compose run --rm --entrypoint promtool prometheus check rules /etc/prometheus/rules/relay-alerts.yml
```

Validar Alertmanager:

```bash
docker compose run --rm --entrypoint amtool alertmanager check-config /etc/alertmanager/alertmanager.yml
```

Validar OpenTelemetry Collector e Tempo:

```bash
docker compose run --rm --entrypoint /otelcol-contrib otel-collector validate --config=/etc/otelcol-contrib/config.yml
docker compose run --rm --entrypoint /tempo tempo --config.file=/etc/tempo/tempo.yml --config.verify=true
```

Validar Loki e Alloy:

```bash
docker compose run --rm --entrypoint /usr/bin/loki loki --config.file=/etc/loki/loki.yml --verify-config=true
docker compose run --rm --entrypoint /bin/alloy alloy validate /etc/alloy/config.alloy
```
