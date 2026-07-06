from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.enums import EventStatus
from app.db.base import Base
from app.models import DeadLetterEvent, Event, EventLog
from app.queues.rabbitmq import retry_limit_exceeded, retry_policy_for_count
from app.workers import event_worker


def test_retry_policy_selection_uses_progressive_backoff() -> None:
    first_retry = retry_policy_for_count(1)
    second_retry = retry_policy_for_count(2)
    third_retry = retry_policy_for_count(3)

    assert first_retry is not None
    assert first_retry.queue_name == "relay.events.retry.10s"
    assert first_retry.ttl_ms == 10_000

    assert second_retry is not None
    assert second_retry.queue_name == "relay.events.retry.30s"
    assert second_retry.ttl_ms == 30_000

    assert third_retry is not None
    assert third_retry.queue_name == "relay.events.retry.5m"
    assert third_retry.ttl_ms == 300_000


def test_retry_count_is_read_from_rabbitmq_headers() -> None:
    assert event_worker._retry_count_from_headers({"x-retry-count": "2"}) == 2
    assert event_worker._retry_count_from_headers({"x-retry-count": 3}) == 3
    assert event_worker._retry_count_from_headers({}) == 0
    assert event_worker._retry_count_from_headers({"x-retry-count": "invalid"}) == 0


def test_retry_limit_is_exceeded_after_configured_retries() -> None:
    assert retry_limit_exceeded(3) is False
    assert retry_limit_exceeded(4) is True


def test_mark_event_dead_letter_updates_status_and_audit_tables(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(event_worker, "SessionLocal", TestingSessionLocal)

    event = Event(
        event_type="customer.created",
        payload={"customer_id": "123"},
        routing_key="events.created",
        correlation_id="correlation-1",
        trace_id="trace-1",
        status=EventStatus.FAILED.value,
    )

    with TestingSessionLocal() as db:
        db.add(event)
        db.commit()
        event_id = event.id

    message = {
        "event_id": event_id,
        "event_type": "customer.created",
        "payload": {"customer_id": "123"},
        "routing_key": "events.created",
        "correlation_id": "correlation-1",
        "trace_id": "trace-1",
    }

    event_worker._mark_event_dead_letter(
        event_id,
        message,
        retry_count=4,
        original_routing_key="events.created",
        error_message="boom",
    )

    with TestingSessionLocal() as db:
        stored_event = db.get(Event, event_id)
        dead_letter = db.scalars(select(DeadLetterEvent)).one()
        log = db.scalars(select(EventLog).where(EventLog.event_id == event_id)).one()

    assert stored_event is not None
    assert stored_event.status == EventStatus.DEAD_LETTER.value
    assert dead_letter.reason == "retry_limit_exceeded"
    assert dead_letter.retry_count == 4
    assert dead_letter.original_routing_key == "events.created"
    assert dead_letter.error_message == "boom"
    assert log.message == "Event sent to dead letter queue"
