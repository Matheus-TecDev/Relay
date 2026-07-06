from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from app.core.enums import EventStatus
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import DeadLetterEvent, Event, EventAttempt, EventLog


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
    with TestClient(app) as test_client:
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


def test_reprocess_dead_letter_event_republishes_original_event(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)
    published_messages: list[tuple[dict, str]] = []

    def fake_publish_event(message: dict, routing_key: str) -> None:
        published_messages.append((message, routing_key))

    monkeypatch.setattr("app.services.dead_letter_service.publish_event", fake_publish_event)

    response = client.post(f"/api/dead-letter-events/{dead_letter_event.id}/reprocess")

    assert response.status_code == 200
    response_payload = response.json()
    assert response_payload["status"] == EventStatus.QUEUED.value
    assert response_payload["routing_key"] == "analytics.page_viewed"
    assert response_payload["correlation_id"] == "correlation-123"
    assert response_payload["trace_id"] == "trace-456"
    assert published_messages == [
        (
            {
                "event_id": dead_letter_event.event_id,
                "event_type": "analytics.page_viewed",
                "payload": {"page": "/pricing"},
                "routing_key": "analytics.page_viewed",
                "correlation_id": "correlation-123",
                "trace_id": "trace-456",
            },
            "analytics.page_viewed",
        )
    ]
    db_session.refresh(dead_letter_event.event)
    assert dead_letter_event.event.status == EventStatus.QUEUED.value


def test_reprocess_dead_letter_event_blocks_recent_duplicate(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)
    published_count = 0

    def fake_publish_event(message: dict, routing_key: str) -> None:
        nonlocal published_count
        published_count += 1

    monkeypatch.setattr("app.services.dead_letter_service.publish_event", fake_publish_event)

    first_response = client.post(f"/api/dead-letter-events/{dead_letter_event.id}/reprocess")
    second_response = client.post(f"/api/dead-letter-events/{dead_letter_event.id}/reprocess")

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert published_count == 1
