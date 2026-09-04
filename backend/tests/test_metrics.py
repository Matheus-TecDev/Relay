from collections.abc import Generator

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
from app.models import DeadLetterEvent, Event, OutboxMessage


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
        event_type="notifications.email_requested",
        payload={"email": "user@example.test"},
        routing_key="notifications.email_requested",
        correlation_id="correlation-metrics",
        trace_id="trace-metrics",
        status=EventStatus.DEAD_LETTER.value,
    )
    db.add(event)
    db.flush()
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
        original_routing_key="notifications.email_requested",
        error_message="smtp failed",
    )
    db.add(dead_letter_event)
    db.commit()
    db.refresh(dead_letter_event)
    return dead_letter_event


def test_metrics_endpoint_exposes_relay_metrics(client: TestClient, db_session: Session) -> None:
    seed_dead_letter_event(db_session)

    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.text
    assert "relay_dead_letter_events_total" in body
    assert "relay_dead_letter_oldest_event_age_seconds" in body
    assert "relay_outbox_messages_failed_total" in body
    assert "relay_outbox_oldest_pending_age_seconds" in body
    assert 'relay_events_by_status{status="dead_letter"} 1.0' in body


def test_event_creation_increments_prometheus_metrics(
    client: TestClient,
    db_session: Session,
) -> None:
    response = client.post(
        "/api/events",
        json={
            "event_type": "customer.created",
            "payload": {"customer_id": "123"},
            "routing_key": "events.created",
            "correlation_id": "correlation-create",
            "trace_id": "trace-create",
        },
    )
    metrics_response = client.get("/metrics")

    assert response.status_code == 201
    assert 'relay_events_created_total{event_type="customer.created",routing_key="events.created"}' in metrics_response.text
    assert "relay_outbox_messages_pending_total" in metrics_response.text
    assert db_session.query(OutboxMessage).count() == 1


def test_dead_letter_reprocess_increments_prometheus_metric(
    client: TestClient,
    db_session: Session,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)

    response = client.post(f"/api/dead-letter-events/{dead_letter_event.id}/reprocess")
    metrics_response = client.get("/metrics")

    assert response.status_code == 200
    assert (
        'relay_dead_letter_reprocess_total{routing_key="notifications.email_requested"}'
        in metrics_response.text
    )
