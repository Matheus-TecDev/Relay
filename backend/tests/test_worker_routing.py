from app.core.config import settings
from app.workers import analytics_worker, audit_worker, event_worker, notification_worker
from app.workers.audit_consumer import main as audit_main
from app.workers.analytics_consumer import main as analytics_main
from app.workers.notification_consumer import main as notification_main


def test_worker_config_can_be_built_from_explicit_queue_and_routing_keys() -> None:
    worker_config = event_worker.build_worker_config(
        name="relay-analytics-worker",
        queue_name=settings.rabbitmq_analytics_queue,
        routing_keys=("analytics.*",),
    )

    assert worker_config.name == "relay-analytics-worker"
    assert worker_config.queue_name == "relay.events.analytics"
    assert worker_config.routing_keys == ("analytics.*",)


def test_worker_accepts_only_matching_routing_keys() -> None:
    audit_config = event_worker.WorkerConfig(
        name="relay-audit-worker",
        queue_name=settings.rabbitmq_audit_queue,
        routing_keys=("events.*", "audit.*"),
    )
    analytics_config = event_worker.WorkerConfig(
        name="relay-analytics-worker",
        queue_name=settings.rabbitmq_analytics_queue,
        routing_keys=("analytics.*",),
    )

    assert event_worker.worker_accepts_routing_key(audit_config, "events.created") is True
    assert event_worker.worker_accepts_routing_key(audit_config, "audit.created") is True
    assert event_worker.worker_accepts_routing_key(audit_config, "analytics.page_viewed") is False
    assert event_worker.worker_accepts_routing_key(analytics_config, "analytics.page_viewed") is True
    assert event_worker.worker_accepts_routing_key(analytics_config, "notifications.email_requested") is False


def test_handler_is_selected_from_original_event_namespace() -> None:
    assert event_worker._resolve_handler("events.created") == audit_worker.handle_event
    assert event_worker._resolve_handler("audit.created") == audit_worker.handle_event
    assert event_worker._resolve_handler("analytics.page_viewed") == analytics_worker.handle_event
    assert event_worker._resolve_handler("notifications.email_requested") == notification_worker.handle_event


def test_original_routing_key_preserves_worker_selection_for_retry_messages() -> None:
    headers = {
        "x-retry-count": 2,
        "x-original-routing-key": "analytics.page_viewed",
    }
    analytics_config = event_worker.WorkerConfig(
        name="relay-analytics-worker",
        queue_name=settings.rabbitmq_analytics_queue,
        routing_keys=("analytics.*",),
    )

    original_routing_key = event_worker._original_routing_key(headers, "retry.30s")

    assert original_routing_key == "analytics.page_viewed"
    assert event_worker.worker_accepts_routing_key(analytics_config, original_routing_key) is True


def test_consumer_entrypoints_delegate_to_worker_runner(monkeypatch) -> None:
    started_workers: list[event_worker.WorkerConfig] = []

    def fake_run_worker(worker_config: event_worker.WorkerConfig) -> None:
        started_workers.append(worker_config)

    monkeypatch.setattr("app.workers.audit_consumer.run_worker", fake_run_worker)
    monkeypatch.setattr("app.workers.analytics_consumer.run_worker", fake_run_worker)
    monkeypatch.setattr("app.workers.notification_consumer.run_worker", fake_run_worker)

    audit_main()
    analytics_main()
    notification_main()

    assert [worker.name for worker in started_workers] == [
        "relay-audit-worker",
        "relay-analytics-worker",
        "relay-notification-worker",
    ]
    assert [worker.queue_name for worker in started_workers] == [
        settings.rabbitmq_audit_queue,
        settings.rabbitmq_analytics_queue,
        settings.rabbitmq_notifications_queue,
    ]
