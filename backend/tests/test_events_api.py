from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth import create_access_token
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
    with TestClient(app, headers={"Authorization": f"Bearer {create_access_token('admin')}"}) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def seed_event(
    db: Session,
    *,
    status: EventStatus = EventStatus.QUEUED,
    event_type: str = "customer.created",
    routing_key: str = "events.created",
) -> Event:
    event = Event(
        event_type=event_type,
        payload={"customer_id": "123"},
        routing_key=routing_key,
        correlation_id=f"correlation-{status.value}",
        trace_id=f"trace-{status.value}",
        status=status.value,
    )
    db.add(event)
    db.flush()
    return event


def test_events_summary_returns_status_counts_and_dead_letter_age(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_event(db_session, status=EventStatus.QUEUED)
    seed_event(db_session, status=EventStatus.PROCESSED)
    dead_letter_event = seed_event(db_session, status=EventStatus.DEAD_LETTER)
    db_session.add(
        DeadLetterEvent(
            event_id=dead_letter_event.id,
            reason="retry_limit_exceeded",
            payload={"event_id": dead_letter_event.id},
            retry_count=4,
            original_routing_key=dead_letter_event.routing_key,
            error_message="handler failed",
            created_at=datetime.now(UTC) - timedelta(minutes=30),
        )
    )
    db_session.commit()

    response = client.get("/api/events/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_events"] == 3
    assert payload["by_status"]["queued"] == 1
    assert payload["by_status"]["processed"] == 1
    assert payload["by_status"]["dead_letter"] == 1
    assert payload["by_status"]["publish_failed"] == 0
    assert payload["dead_letter_total"] == 1
    assert payload["oldest_dead_letter_age_seconds"] >= 0
    assert payload["recent_events_count"] == 3


def test_events_summary_returns_null_oldest_dead_letter_age_when_empty(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_event(db_session, status=EventStatus.PROCESSED)
    db_session.commit()

    response = client.get("/api/events/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dead_letter_total"] == 0
    assert payload["oldest_dead_letter_age_seconds"] is None


def test_get_event_returns_detail_with_attempts_logs_and_dead_letter_entries(
    client: TestClient,
    db_session: Session,
) -> None:
    event = seed_event(db_session, status=EventStatus.DEAD_LETTER)
    db_session.add(
        EventAttempt(
            event_id=event.id,
            attempt_number=1,
            status=EventStatus.FAILED.value,
            error_message="handler failed",
        )
    )
    db_session.add(
        EventLog(
            event_id=event.id,
            level="error",
            message="Event failed",
            log_metadata={"reason": "handler failed"},
        )
    )
    db_session.add(
        DeadLetterEvent(
            event_id=event.id,
            reason="retry_limit_exceeded",
            payload={"event_id": event.id},
            retry_count=4,
            original_routing_key=event.routing_key,
            error_message="handler failed",
        )
    )
    db_session.commit()

    response = client.get(f"/api/events/{event.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == event.id
    assert payload["status"] == EventStatus.DEAD_LETTER.value
    assert payload["attempts"][0]["attempt_number"] == 1
    assert payload["logs"][0]["message"] == "Event failed"
    assert payload["dead_letter_entries"][0]["reason"] == "retry_limit_exceeded"


def test_get_event_returns_404_for_unknown_event(client: TestClient) -> None:
    response = client.get("/api/events/not-found")

    assert response.status_code == 404
    assert response.json() == {"detail": "Event not found."}
