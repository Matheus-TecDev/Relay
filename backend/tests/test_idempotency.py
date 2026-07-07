from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import EventProcessingStatus, EventStatus
from app.db.base import Base
from app.models import Event, EventAttempt, EventProcessingState
from app.services.idempotency_service import stuck_processing_events
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


def _seed_event(session_factory, status: str = EventStatus.QUEUED.value) -> str:
    event = Event(
        event_type="customer.created",
        payload={"customer_id": "123"},
        routing_key="events.created",
        correlation_id="correlation-idempotency",
        trace_id="trace-idempotency",
        status=status,
    )
    with session_factory() as db:
        db.add(event)
        db.commit()
        return event.id


def _message(event_id: str) -> dict:
    return {
        "event_id": event_id,
        "event_type": "customer.created",
        "payload": {"customer_id": "123"},
        "routing_key": "events.created",
        "correlation_id": "correlation-idempotency",
        "trace_id": "trace-idempotency",
    }


def test_duplicate_event_is_skipped_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(event_worker, "SessionLocal", session_factory)
    calls: list[str] = []

    def handler(message: dict) -> None:
        calls.append(message["event_id"])

    monkeypatch.setitem(event_worker.HANDLERS, "events", handler)
    event_id = _seed_event(session_factory)

    event_worker.process_event(_message(event_id), "events.created", worker_name="worker-a")
    event_worker.process_event(_message(event_id), "events.created", worker_name="worker-b")

    with session_factory() as db:
        attempts = list(db.scalars(select(EventAttempt).where(EventAttempt.event_id == event_id)).all())
        state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event_id))

    assert calls == [event_id]
    assert len(attempts) == 1
    assert attempts[0].status == EventStatus.PROCESSED.value
    assert state is not None
    assert state.status == EventProcessingStatus.PROCESSED.value


def test_second_worker_skips_event_already_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(event_worker, "SessionLocal", session_factory)
    event_id = _seed_event(session_factory)

    with session_factory() as db:
        db.add(
            EventProcessingState(
                event_id=event_id,
                status=EventProcessingStatus.PROCESSING.value,
                worker_name="worker-a",
                routing_key="events.created",
                processing_started_at=datetime.now(UTC),
            )
        )
        db.commit()

    def handler(message: dict) -> None:
        raise AssertionError("duplicate worker must not execute handler")

    monkeypatch.setitem(event_worker.HANDLERS, "events", handler)
    event_worker.process_event(_message(event_id), "events.created", worker_name="worker-b")

    with session_factory() as db:
        attempts = list(db.scalars(select(EventAttempt).where(EventAttempt.event_id == event_id)).all())
        state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event_id))

    assert attempts == []
    assert state is not None
    assert state.status == EventProcessingStatus.PROCESSING.value
    assert state.worker_name == "worker-a"


def test_retry_message_for_processed_event_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(event_worker, "SessionLocal", session_factory)
    event_id = _seed_event(session_factory, EventStatus.PROCESSED.value)

    with session_factory() as db:
        db.add(
            EventProcessingState(
                event_id=event_id,
                status=EventProcessingStatus.PROCESSED.value,
                worker_name="worker-a",
                routing_key="events.created",
                processed_at=datetime.now(UTC),
            )
        )
        db.commit()

    def handler(message: dict) -> None:
        raise AssertionError("retry for processed event must not execute handler")

    monkeypatch.setitem(event_worker.HANDLERS, "events", handler)
    event_worker.process_event(_message(event_id), "events.created", retry_count=2, worker_name="worker-b")

    with session_factory() as db:
        attempts = list(db.scalars(select(EventAttempt).where(EventAttempt.event_id == event_id)).all())

    assert attempts == []


def test_failure_before_processed_marks_state_failed_and_retry_can_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(event_worker, "SessionLocal", session_factory)
    event_id = _seed_event(session_factory)

    def failing_handler(message: dict) -> None:
        raise RuntimeError("temporary failure")

    monkeypatch.setitem(event_worker.HANDLERS, "events", failing_handler)
    with pytest.raises(RuntimeError):
        event_worker.process_event(_message(event_id), "events.created", worker_name="worker-a")

    with session_factory() as db:
        state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event_id))
        event = db.get(Event, event_id)

    assert state is not None
    assert state.status == EventProcessingStatus.FAILED.value
    assert event is not None
    assert event.status == EventStatus.FAILED.value

    calls: list[str] = []

    def successful_handler(message: dict) -> None:
        calls.append(message["event_id"])

    monkeypatch.setitem(event_worker.HANDLERS, "events", successful_handler)
    event_worker.process_event(_message(event_id), "events.created", retry_count=1, worker_name="worker-a")

    with session_factory() as db:
        state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event_id))

    assert calls == [event_id]
    assert state is not None
    assert state.status == EventProcessingStatus.PROCESSED.value


def test_stuck_processing_event_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    session_factory = _session_factory()
    event_id = _seed_event(session_factory)
    monkeypatch.setattr(
        "app.services.idempotency_service.settings.idempotency_processing_timeout_seconds",
        60,
    )

    with session_factory() as db:
        db.add(
            EventProcessingState(
                event_id=event_id,
                status=EventProcessingStatus.PROCESSING.value,
                worker_name="worker-a",
                routing_key="events.created",
                processing_started_at=datetime.now(UTC) - timedelta(minutes=10),
            )
        )
        db.commit()

        stuck = stuck_processing_events(db)

    assert [state.event_id for state in stuck] == [event_id]
