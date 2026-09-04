from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth import create_access_token
from app.core.enums import EventProcessingStatus, EventStatus, OutboxStatus
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import DeadLetterEvent, Event, EventAttempt, EventLog, EventProcessingState, OutboxMessage
from app.services.outbox_service import acquire_next_outbox_message, publish_outbox_message


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as session:
        yield session

    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, headers={"Authorization": f"Bearer {create_access_token('admin')}"}) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def seed_dead_letter_event(db: Session) -> DeadLetterEvent:
    event = Event(
        event_type="analytics.page_viewed",
        payload={"page": "/pricing"},
        routing_key="analytics.page_viewed",
        correlation_id="correlation-123",
        trace_id="trace-456",
        status=EventStatus.DEAD_LETTER.value,
    )
    db.add(event)
    db.flush()
    db.add(
        EventAttempt(
            event_id=event.id,
            attempt_number=1,
            status=EventStatus.FAILED.value,
            error_message="handler failed",
        )
    )
    db.add(
        EventLog(
            event_id=event.id,
            level="error",
            message="Event sent to dead letter queue",
            log_metadata={"error": "handler failed"},
        )
    )
    db.add(
        EventProcessingState(
            event_id=event.id,
            status=EventProcessingStatus.DEAD_LETTERED.value,
            worker_name="relay-audit-worker",
            routing_key="analytics.page_viewed",
            attempt_count=4,
            error_message="handler failed",
        )
    )
    db.add(
        OutboxMessage(
            event_id=event.id,
            exchange="relay.events",
            routing_key="analytics.page_viewed",
            payload={
                "event_id": event.id,
                "event_type": event.event_type,
                "payload": event.payload,
                "routing_key": event.routing_key,
                "correlation_id": event.correlation_id,
                "trace_id": event.trace_id,
            },
            headers={},
            status=OutboxStatus.PUBLISHED.value,
        )
    )
    dead_letter_event = DeadLetterEvent(
        event_id=event.id,
        reason="retry_limit_exceeded",
        payload={
            "event_id": event.id,
            "event_type": event.event_type,
            "payload": event.payload,
            "routing_key": event.routing_key,
            "correlation_id": event.correlation_id,
            "trace_id": event.trace_id,
        },
        retry_count=4,
        original_routing_key="analytics.page_viewed",
        error_message="handler failed",
    )
    db.add(dead_letter_event)
    db.commit()
    db.refresh(dead_letter_event)
    return dead_letter_event


def test_list_dead_letter_events_returns_operational_fields(client: TestClient, db_session: Session) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)

    response = client.get("/api/dead-letter-events")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == dead_letter_event.id
    assert payload[0]["event_id"] == dead_letter_event.event_id
    assert payload[0]["reason"] == "retry_limit_exceeded"
    assert payload[0]["error_message"] == "handler failed"
    assert payload[0]["retry_count"] == 4
    assert payload[0]["original_routing_key"] == "analytics.page_viewed"
    assert payload[0]["correlation_id"] == "correlation-123"
    assert payload[0]["trace_id"] == "trace-456"
    assert payload[0]["event_status"] == EventStatus.DEAD_LETTER.value


def test_get_dead_letter_event_returns_detail(client: TestClient, db_session: Session) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)

    response = client.get(f"/api/dead-letter-events/{dead_letter_event.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == dead_letter_event.id
    assert payload["payload"]["correlation_id"] == "correlation-123"
    assert payload["event"]["trace_id"] == "trace-456"
    assert len(payload["attempts"]) == 1
    assert len(payload["logs"]) == 1


def test_reprocess_dead_letter_event_queues_original_event_in_outbox(
    client: TestClient,
    db_session: Session,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)

    response = client.post(f"/api/dead-letter-events/{dead_letter_event.id}/reprocess")

    assert response.status_code == 200
    response_payload = response.json()
    assert response_payload["status"] == EventStatus.QUEUED.value
    assert response_payload["routing_key"] == "analytics.page_viewed"
    assert response_payload["correlation_id"] == "correlation-123"
    assert response_payload["trace_id"] == "trace-456"
    db_session.refresh(dead_letter_event.event)
    assert dead_letter_event.event.status == EventStatus.QUEUED.value

    processing_state = db_session.scalar(
        select(EventProcessingState).where(EventProcessingState.event_id == dead_letter_event.event_id)
    )
    assert processing_state is not None
    assert processing_state.status == EventProcessingStatus.PENDING.value
    assert processing_state.worker_name is None
    assert processing_state.error_message is None

    outbox_message = db_session.scalar(
        select(OutboxMessage).where(OutboxMessage.event_id == dead_letter_event.event_id)
    )
    assert outbox_message is not None
    assert outbox_message.status == OutboxStatus.PENDING.value
    assert outbox_message.attempt_count == 0
    assert outbox_message.published_at is None
    assert outbox_message.last_error is None
    assert outbox_message.routing_key == "analytics.page_viewed"
    assert outbox_message.headers == {"x-reprocess-dead-letter-event-id": dead_letter_event.id}
    assert outbox_message.payload == {
        "event_id": dead_letter_event.event_id,
        "event_type": "analytics.page_viewed",
        "payload": {"page": "/pricing"},
        "routing_key": "analytics.page_viewed",
        "correlation_id": "correlation-123",
        "trace_id": "trace-456",
    }


def test_reprocess_dead_letter_event_blocks_recent_duplicate(
    client: TestClient,
    db_session: Session,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)

    first_response = client.post(f"/api/dead-letter-events/{dead_letter_event.id}/reprocess")
    second_response = client.post(f"/api/dead-letter-events/{dead_letter_event.id}/reprocess")

    assert first_response.status_code == 200
    assert second_response.status_code == 409

    reprocess_logs = list(
        db_session.scalars(
            select(EventLog)
            .where(EventLog.event_id == dead_letter_event.event_id)
            .where(EventLog.message == "Manual DLQ reprocess queued in outbox")
        ).all()
    )
    assert len(reprocess_logs) == 1


def test_reprocess_dead_letter_event_blocks_already_processed_event(
    client: TestClient,
    db_session: Session,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)
    event = dead_letter_event.event
    assert event is not None
    event.status = EventStatus.PROCESSED.value
    db_session.commit()

    response = client.post(f"/api/dead-letter-events/{dead_letter_event.id}/reprocess")

    assert response.status_code == 409
    outbox_message = db_session.scalar(
        select(OutboxMessage).where(OutboxMessage.event_id == dead_letter_event.event_id)
    )
    assert outbox_message is not None
    assert outbox_message.status == OutboxStatus.PUBLISHED.value


def test_failed_outbox_publication_after_reprocess_remains_retryable(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)

    response = client.post(f"/api/dead-letter-events/{dead_letter_event.id}/reprocess")
    assert response.status_code == 200

    def failing_publish_message(*args, **kwargs) -> None:
        raise RuntimeError("rabbitmq unavailable")

    monkeypatch.setattr("app.services.outbox_service.publish_message", failing_publish_message)
    outbox_message = acquire_next_outbox_message(db_session, "publisher-a")
    assert outbox_message is not None
    publish_outbox_message(db_session, outbox_message, "publisher-a")

    stored_message = db_session.scalar(
        select(OutboxMessage).where(OutboxMessage.event_id == dead_letter_event.event_id)
    )
    assert stored_message is not None
    assert stored_message.status == OutboxStatus.FAILED.value
    assert stored_message.published_at is None
    assert stored_message.last_error == "rabbitmq unavailable"
    assert stored_message.next_attempt_at is not None
    db_session.refresh(dead_letter_event.event)
    assert dead_letter_event.event.status == EventStatus.PUBLISH_FAILED.value
