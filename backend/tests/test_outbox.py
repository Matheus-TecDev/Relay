from datetime import UTC, datetime, timedelta

import pika
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import EventProcessingStatus, EventStatus, OutboxStatus
from app.db.base import Base
from app.models import Event, EventProcessingState, OutboxMessage
from app.services.event_service import EventService
from app.services.outbox_service import (
    acquire_next_outbox_message,
    publish_outbox_message,
    recover_stuck_publishing_messages,
)
from app.schemas.event import EventCreate
from app.workers import event_worker


def _session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return testing_session_local


def _create_event_with_outbox(session_factory) -> tuple[str, str]:
    with session_factory() as db:
        event = EventService(db).create_event(
            EventCreate(
                event_type="customer.created",
                payload={"customer_id": "123"},
                routing_key="events.created",
                correlation_id="correlation-outbox",
                trace_id="trace-outbox",
            )
        )
        outbox_message = db.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event.id))
        assert outbox_message is not None
        return event.id, outbox_message.id


def test_event_created_generates_outbox_in_same_transaction() -> None:
    session_factory = _session_factory()
    event_id, outbox_message_id = _create_event_with_outbox(session_factory)

    with session_factory() as db:
        event = db.get(Event, event_id)
        processing_state = db.scalar(
            select(EventProcessingState).where(EventProcessingState.event_id == event_id)
        )
        outbox_message = db.get(OutboxMessage, outbox_message_id)

    assert event is not None
    assert event.status == EventStatus.RECEIVED.value
    assert processing_state is not None
    assert processing_state.status == EventProcessingStatus.PENDING.value
    assert outbox_message is not None
    assert outbox_message.status == OutboxStatus.PENDING.value
    assert outbox_message.payload["event_id"] == event_id


def test_failed_publication_keeps_outbox_message_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    _, outbox_message_id = _create_event_with_outbox(session_factory)

    def failing_publish_message(*args, **kwargs) -> None:
        raise RuntimeError("rabbitmq unavailable")

    monkeypatch.setattr("app.services.outbox_service.publish_message", failing_publish_message)

    with session_factory() as db:
        outbox_message = acquire_next_outbox_message(db, "publisher-a")
        assert outbox_message is not None
        publish_outbox_message(db, outbox_message, "publisher-a")

    with session_factory() as db:
        stored_message = db.get(OutboxMessage, outbox_message_id)

    assert stored_message is not None
    assert stored_message.status == OutboxStatus.FAILED.value
    assert stored_message.attempt_count == 1
    assert stored_message.last_error == "rabbitmq unavailable"
    assert stored_message.next_attempt_at is not None


def test_unroutable_publication_keeps_outbox_message_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    _, outbox_message_id = _create_event_with_outbox(session_factory)

    def unroutable_publish_message(*args, **kwargs) -> None:
        raise pika.exceptions.UnroutableError([])

    monkeypatch.setattr("app.services.outbox_service.publish_message", unroutable_publish_message)

    with session_factory() as db:
        outbox_message = acquire_next_outbox_message(db, "publisher-a")
        assert outbox_message is not None
        publish_outbox_message(db, outbox_message, "publisher-a")

    with session_factory() as db:
        stored_message = db.get(OutboxMessage, outbox_message_id)
        assert stored_message is not None
        event = db.get(Event, stored_message.event_id)

    assert stored_message.status == OutboxStatus.FAILED.value
    assert stored_message.published_at is None
    assert stored_message.last_error == "0 unroutable message(s) returned"
    assert stored_message.next_attempt_at is not None
    assert event is not None
    assert event.status == EventStatus.PUBLISH_FAILED.value


def test_successful_publication_marks_outbox_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    event_id, outbox_message_id = _create_event_with_outbox(session_factory)
    published: list[tuple[str, str, dict]] = []

    def fake_publish_message(exchange: str, routing_key: str, message: dict, headers: dict | None = None) -> None:
        del headers
        published.append((exchange, routing_key, message))

    monkeypatch.setattr("app.services.outbox_service.publish_message", fake_publish_message)

    with session_factory() as db:
        outbox_message = acquire_next_outbox_message(db, "publisher-a")
        assert outbox_message is not None
        publish_outbox_message(db, outbox_message, "publisher-a")

    with session_factory() as db:
        stored_message = db.get(OutboxMessage, outbox_message_id)
        event = db.get(Event, event_id)

    assert len(published) == 1
    assert stored_message is not None
    assert stored_message.status == OutboxStatus.PUBLISHED.value
    assert stored_message.published_at is not None
    assert event is not None
    assert event.status == EventStatus.QUEUED.value


def test_two_publishers_do_not_publish_same_message() -> None:
    session_factory = _session_factory()
    _, outbox_message_id = _create_event_with_outbox(session_factory)

    with session_factory() as db:
        first_message = acquire_next_outbox_message(db, "publisher-a")
        assert first_message is not None
        assert first_message.id == outbox_message_id
        db.commit()

    with session_factory() as db:
        second_message = acquire_next_outbox_message(db, "publisher-b")

    assert second_message is None


def test_stuck_publishing_message_returns_to_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    session_factory = _session_factory()
    _, outbox_message_id = _create_event_with_outbox(session_factory)
    monkeypatch.setattr("app.services.outbox_service.settings.outbox_publishing_timeout_seconds", 60)

    with session_factory() as db:
        outbox_message = db.get(OutboxMessage, outbox_message_id)
        assert outbox_message is not None
        outbox_message.status = OutboxStatus.PUBLISHING.value
        outbox_message.locked_at = datetime.now(UTC) - timedelta(minutes=10)
        outbox_message.locked_by = "publisher-a"
        db.commit()

    with session_factory() as db:
        recovered = recover_stuck_publishing_messages(db)
        recovered_ids = [message.id for message in recovered]
        db.commit()

    with session_factory() as db:
        stored_message = db.get(OutboxMessage, outbox_message_id)

    assert recovered_ids == [outbox_message_id]
    assert stored_message is not None
    assert stored_message.status == OutboxStatus.FAILED.value
    assert stored_message.next_attempt_at is not None
    assert stored_message.locked_by is None


def test_duplicate_outbox_delivery_does_not_break_consumer_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    event_id, outbox_message_id = _create_event_with_outbox(session_factory)
    monkeypatch.setattr(event_worker, "SessionLocal", session_factory)
    calls: list[str] = []

    def handler(message: dict) -> None:
        calls.append(message["event_id"])

    monkeypatch.setitem(event_worker.HANDLERS, "events", handler)

    with session_factory() as db:
        outbox_message = db.get(OutboxMessage, outbox_message_id)
        assert outbox_message is not None
        message = outbox_message.payload

    event_worker.process_event(message, "events.created", worker_name="worker-a")
    event_worker.process_event(message, "events.created", worker_name="worker-b")

    assert calls == [event_id]
