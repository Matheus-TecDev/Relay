import json
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.enums import EventProcessingStatus, EventStatus
from app.db.base import Base
from app.models import DeadLetterEvent, Event, EventLog, EventProcessingState
from app.queues.rabbitmq import RetryPolicy, retry_limit_exceeded, retry_policy_for_count
from app.workers import event_worker


class FakeChannel:
    def __init__(self) -> None:
        self.acks: list[int] = []
        self.nacks: list[tuple[int, bool]] = []
        self.stop_consuming_calls = 0

    def basic_ack(self, delivery_tag: int) -> None:
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag: int, requeue: bool) -> None:
        self.nacks.append((delivery_tag, requeue))

    def stop_consuming(self) -> None:
        self.stop_consuming_calls += 1


def _session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return testing_session_local


def _seed_event(session_factory) -> tuple[str, dict]:
    event = Event(
        event_type="customer.created",
        payload={"customer_id": "123"},
        routing_key="events.created",
        correlation_id="correlation-1",
        trace_id="trace-1",
        status=EventStatus.QUEUED.value,
    )
    with session_factory() as db:
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
    return event_id, message


def _method(delivery_tag: int = 42, routing_key: str = "events.created") -> SimpleNamespace:
    return SimpleNamespace(delivery_tag=delivery_tag, routing_key=routing_key)


def _properties(headers: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(headers=headers or {})


def _body(message: dict) -> bytes:
    return json.dumps(message).encode("utf-8")


def _failing_handler(message: dict) -> None:
    del message
    raise RuntimeError("handler failed")


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
    TestingSessionLocal = _session_factory()
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


def test_failed_processing_with_retry_publish_success_acks_original_message(monkeypatch) -> None:
    session_factory = _session_factory()
    event_id, message = _seed_event(session_factory)
    channel = FakeChannel()
    published_retries: list[tuple[dict, str, int, str]] = []
    monkeypatch.setattr(event_worker, "SessionLocal", session_factory)
    monkeypatch.setitem(event_worker.HANDLERS, "events", _failing_handler)

    def fake_publish_retry_event(
        published_message: dict,
        original_routing_key: str,
        retry_count: int,
        error_message: str,
    ) -> RetryPolicy:
        published_retries.append((published_message, original_routing_key, retry_count, error_message))
        return RetryPolicy(1, "relay.events.retry.10s", "retry.10s", 10_000)

    monkeypatch.setattr(event_worker, "publish_retry_event", fake_publish_retry_event)

    event_worker.handle_message(
        channel,
        _method(),
        _properties(),
        _body(message),
        event_worker.WorkerConfig("worker-a", "relay.events.audit", ("events.*",)),
    )

    assert channel.acks == [42]
    assert channel.nacks == []
    assert channel.stop_consuming_calls == 0
    assert published_retries == [(message, "events.created", 1, "handler failed")]

    with session_factory() as db:
        event = db.get(Event, event_id)
        retry_log = db.scalar(select(EventLog).where(EventLog.message == "Event sent to retry queue"))
        processing_state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event_id))

    assert event is not None
    assert event.status == EventStatus.QUEUED.value
    assert retry_log is not None
    assert processing_state is not None
    assert processing_state.status == EventProcessingStatus.FAILED.value


def test_failed_processing_with_retry_publish_failure_nacks_and_does_not_record_retry(
    monkeypatch,
) -> None:
    session_factory = _session_factory()
    event_id, message = _seed_event(session_factory)
    channel = FakeChannel()
    monkeypatch.setattr(event_worker, "SessionLocal", session_factory)
    monkeypatch.setitem(event_worker.HANDLERS, "events", _failing_handler)

    def failing_publish_retry_event(*args, **kwargs) -> RetryPolicy:
        del args, kwargs
        raise RuntimeError("retry broker unavailable")

    monkeypatch.setattr(event_worker, "publish_retry_event", failing_publish_retry_event)

    event_worker.handle_message(
        channel,
        _method(),
        _properties(),
        _body(message),
        event_worker.WorkerConfig("worker-a", "relay.events.audit", ("events.*",)),
    )

    assert channel.acks == []
    assert channel.nacks == [(42, True)]
    assert channel.stop_consuming_calls == 1

    with session_factory() as db:
        event = db.get(Event, event_id)
        retry_log = db.scalar(select(EventLog).where(EventLog.message == "Event sent to retry queue"))
        dead_letter = db.scalar(select(DeadLetterEvent))
        processing_state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event_id))

    assert event is not None
    assert event.status == EventStatus.FAILED.value
    assert retry_log is None
    assert dead_letter is None
    assert processing_state is not None
    assert processing_state.status == EventProcessingStatus.FAILED.value


def test_failed_processing_with_dlq_publish_success_acks_original_message(monkeypatch) -> None:
    session_factory = _session_factory()
    event_id, message = _seed_event(session_factory)
    channel = FakeChannel()
    published_dead_letters: list[tuple[dict, str, int, str]] = []
    monkeypatch.setattr(event_worker, "SessionLocal", session_factory)
    monkeypatch.setitem(event_worker.HANDLERS, "events", _failing_handler)

    def fake_publish_dead_letter_event(
        published_message: dict,
        original_routing_key: str,
        retry_count: int,
        error_message: str,
    ) -> None:
        published_dead_letters.append((published_message, original_routing_key, retry_count, error_message))

    monkeypatch.setattr(event_worker, "publish_dead_letter_event", fake_publish_dead_letter_event)

    event_worker.handle_message(
        channel,
        _method(),
        _properties({"x-retry-count": 3, "x-original-routing-key": "events.created"}),
        _body(message),
        event_worker.WorkerConfig("worker-a", "relay.events.audit", ("events.*",)),
    )

    assert channel.acks == [42]
    assert channel.nacks == []
    assert channel.stop_consuming_calls == 0
    assert published_dead_letters == [(message, "events.created", 4, "handler failed")]

    with session_factory() as db:
        event = db.get(Event, event_id)
        dead_letter = db.scalar(select(DeadLetterEvent))
        processing_state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event_id))

    assert event is not None
    assert event.status == EventStatus.DEAD_LETTER.value
    assert dead_letter is not None
    assert dead_letter.retry_count == 4
    assert processing_state is not None
    assert processing_state.status == EventProcessingStatus.DEAD_LETTERED.value


def test_failed_processing_with_dlq_publish_failure_nacks_and_does_not_record_dead_letter(
    monkeypatch,
) -> None:
    session_factory = _session_factory()
    event_id, message = _seed_event(session_factory)
    channel = FakeChannel()
    monkeypatch.setattr(event_worker, "SessionLocal", session_factory)
    monkeypatch.setitem(event_worker.HANDLERS, "events", _failing_handler)

    def failing_publish_dead_letter_event(*args, **kwargs) -> None:
        del args, kwargs
        raise RuntimeError("dlq broker unavailable")

    monkeypatch.setattr(event_worker, "publish_dead_letter_event", failing_publish_dead_letter_event)

    event_worker.handle_message(
        channel,
        _method(),
        _properties({"x-retry-count": 3, "x-original-routing-key": "events.created"}),
        _body(message),
        event_worker.WorkerConfig("worker-a", "relay.events.audit", ("events.*",)),
    )

    assert channel.acks == []
    assert channel.nacks == [(42, True)]
    assert channel.stop_consuming_calls == 1

    with session_factory() as db:
        event = db.get(Event, event_id)
        dead_letter = db.scalar(select(DeadLetterEvent))
        dead_letter_log = db.scalar(select(EventLog).where(EventLog.message == "Event sent to dead letter queue"))
        processing_state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event_id))

    assert event is not None
    assert event.status == EventStatus.FAILED.value
    assert dead_letter is None
    assert dead_letter_log is None
    assert processing_state is not None
    assert processing_state.status == EventProcessingStatus.FAILED.value


def test_redelivery_after_retry_publish_failure_can_be_reprocessed_idempotently(monkeypatch) -> None:
    session_factory = _session_factory()
    event_id, message = _seed_event(session_factory)
    first_channel = FakeChannel()
    second_channel = FakeChannel()
    publish_calls = 0
    monkeypatch.setattr(event_worker, "SessionLocal", session_factory)
    monkeypatch.setitem(event_worker.HANDLERS, "events", _failing_handler)

    def flaky_publish_retry_event(*args, **kwargs) -> RetryPolicy:
        nonlocal publish_calls
        del args, kwargs
        publish_calls += 1
        if publish_calls == 1:
            raise RuntimeError("retry broker unavailable")
        return RetryPolicy(1, "relay.events.retry.10s", "retry.10s", 10_000)

    monkeypatch.setattr(event_worker, "publish_retry_event", flaky_publish_retry_event)

    worker_config = event_worker.WorkerConfig("worker-a", "relay.events.audit", ("events.*",))
    event_worker.handle_message(first_channel, _method(), _properties(), _body(message), worker_config)
    event_worker.handle_message(second_channel, _method(delivery_tag=43), _properties(), _body(message), worker_config)

    assert first_channel.acks == []
    assert first_channel.nacks == [(42, True)]
    assert second_channel.acks == [43]
    assert second_channel.nacks == []

    with session_factory() as db:
        event = db.get(Event, event_id)
        attempts = list(db.scalars(select(EventLog).where(EventLog.message == "Event attempt started")).all())
        retry_logs = list(db.scalars(select(EventLog).where(EventLog.message == "Event sent to retry queue")).all())
        processing_state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event_id))

    assert event is not None
    assert event.status == EventStatus.QUEUED.value
    assert len(attempts) == 2
    assert len(retry_logs) == 1
    assert processing_state is not None
    assert processing_state.status == EventProcessingStatus.FAILED.value
