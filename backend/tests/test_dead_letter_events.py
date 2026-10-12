from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth import create_access_token
from app.core.enums import EventProcessingStatus, EventStatus, OutboxStatus
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import DeadLetterEvent, Event, EventAttempt, EventLog, EventProcessingState, OutboxMessage
from app.services.dead_letter_service import DeadLetterService, ReprocessLockUnavailableError
from app.services.outbox_service import acquire_next_outbox_message, publish_outbox_message

BATCH_URL = "/api/dead-letter-events/reprocess-batch"
REPROCESS_LOG_MESSAGE = "Manual DLQ reprocess queued in outbox"


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


def seed_extra_dead_letter_entry(db: Session, event_id: str) -> DeadLetterEvent:
    dead_letter_event = DeadLetterEvent(
        event_id=event_id,
        reason="retry_limit_exceeded",
        payload={"event_id": event_id},
        retry_count=4,
        original_routing_key="analytics.page_viewed",
        error_message="handler failed again",
    )
    db.add(dead_letter_event)
    db.commit()
    db.refresh(dead_letter_event)
    return dead_letter_event


def reprocess_success_metric() -> float:
    return REGISTRY.get_sample_value(
        "relay_dead_letter_reprocess_total",
        {"routing_key": "analytics.page_viewed"},
    ) or 0.0


def reprocess_log_count(db: Session, event_id: str) -> int:
    return db.scalar(
        select(func.count(EventLog.id))
        .where(EventLog.event_id == event_id)
        .where(EventLog.message == REPROCESS_LOG_MESSAGE)
    ) or 0


def outbox_for(db: Session, event_id: str) -> OutboxMessage:
    db.expire_all()
    outbox_message = db.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
    assert outbox_message is not None
    return outbox_message


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"dead_letter_event_ids": []},
        {"dead_letter_event_ids": "not-a-list"},
        {"dead_letter_event_ids": ["not-a-uuid"]},
        {"dead_letter_event_ids": [None]},
        {"dead_letter_event_ids": [str(uuid4()) for _ in range(21)]},
    ],
)
def test_batch_reprocess_rejects_invalid_payload(client: TestClient, payload: dict) -> None:
    response = client.post(BATCH_URL, json=payload)

    assert response.status_code == 422


def test_batch_reprocess_accepts_maximum_batch_size(client: TestClient) -> None:
    response = client.post(BATCH_URL, json={"dead_letter_event_ids": [str(uuid4()) for _ in range(20)]})

    assert response.status_code == 200
    assert response.json()["summary"]["not_found"] == 20


def test_batch_reprocess_rejects_duplicate_ids_without_side_effects(
    client: TestClient,
    db_session: Session,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)

    for ids in (
        [dead_letter_event.id, dead_letter_event.id],
        [dead_letter_event.id, dead_letter_event.id.upper()],
    ):
        response = client.post(BATCH_URL, json={"dead_letter_event_ids": ids})
        assert response.status_code == 422

    db_session.refresh(dead_letter_event.event)
    assert dead_letter_event.event.status == EventStatus.DEAD_LETTER.value
    assert outbox_for(db_session, dead_letter_event.event_id).status == OutboxStatus.PUBLISHED.value


def test_batch_reprocess_requires_authentication(client: TestClient, db_session: Session) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)

    response = client.post(
        BATCH_URL,
        json={"dead_letter_event_ids": [dead_letter_event.id]},
        headers={"Authorization": ""},
    )

    assert response.status_code == 401
    db_session.refresh(dead_letter_event.event)
    assert dead_letter_event.event.status == EventStatus.DEAD_LETTER.value


def test_batch_reprocess_reports_missing_dead_letter_event(client: TestClient) -> None:
    missing_id = str(uuid4())

    response = client.post(BATCH_URL, json={"dead_letter_event_ids": [missing_id]})

    assert response.status_code == 200
    assert response.json() == {
        "summary": {"total": 1, "queued": 0, "conflict": 0, "not_found": 1, "failed": 0},
        "results": [
            {
                "dead_letter_event_id": missing_id,
                "status": "not_found",
                "reason": "dead_letter_event_not_found",
                "message": "Dead letter event not found.",
                "event_id": None,
                "routing_key": None,
                "correlation_id": None,
                "trace_id": None,
            }
        ],
    }


def test_batch_reprocess_blocks_already_processed_events(client: TestClient, db_session: Session) -> None:
    left_dead_letter = seed_dead_letter_event(db_session)
    left_dead_letter.event.status = EventStatus.PROCESSED.value
    processed_state = seed_dead_letter_event(db_session)
    state = db_session.scalar(
        select(EventProcessingState).where(EventProcessingState.event_id == processed_state.event_id)
    )
    assert state is not None
    state.status = EventProcessingStatus.PROCESSED.value
    db_session.commit()

    response = client.post(
        BATCH_URL,
        json={"dead_letter_event_ids": [left_dead_letter.id, processed_state.id]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {"total": 2, "queued": 0, "conflict": 2, "not_found": 0, "failed": 0}
    assert [(item["status"], item["reason"]) for item in payload["results"]] == [
        ("conflict", "not_dead_letter"),
        ("conflict", "already_processed"),
    ]
    assert payload["results"][1]["event_id"] == processed_state.event_id
    for dead_letter_event in (left_dead_letter, processed_state):
        assert outbox_for(db_session, dead_letter_event.event_id).status == OutboxStatus.PUBLISHED.value
        assert reprocess_log_count(db_session, dead_letter_event.event_id) == 0
    db_session.refresh(processed_state.event)
    assert processed_state.event.status == EventStatus.DEAD_LETTER.value


def test_batch_reprocess_partial_success_preserves_outbox_and_identifiers(
    client: TestClient,
    db_session: Session,
) -> None:
    first = seed_dead_letter_event(db_session)
    processed = seed_dead_letter_event(db_session)
    processed.event.status = EventStatus.PROCESSED.value
    db_session.commit()
    second = seed_dead_letter_event(db_session)
    missing_id = str(uuid4())
    events_before = db_session.scalar(select(func.count(Event.id)))
    outbox_before = db_session.scalar(select(func.count(OutboxMessage.id)))
    metric_before = reprocess_success_metric()

    response = client.post(
        BATCH_URL,
        json={"dead_letter_event_ids": [first.id, processed.id, missing_id, second.id]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {"total": 4, "queued": 2, "conflict": 1, "not_found": 1, "failed": 0}
    assert [item["dead_letter_event_id"] for item in payload["results"]] == [
        first.id,
        processed.id,
        missing_id,
        second.id,
    ]
    assert [item["status"] for item in payload["results"]] == ["queued", "conflict", "not_found", "queued"]
    assert payload["results"][0] == {
        "dead_letter_event_id": first.id,
        "status": "queued",
        "reason": None,
        "message": None,
        "event_id": first.event_id,
        "routing_key": "analytics.page_viewed",
        "correlation_id": "correlation-123",
        "trace_id": "trace-456",
    }

    assert db_session.scalar(select(func.count(Event.id))) == events_before
    assert db_session.scalar(select(func.count(OutboxMessage.id))) == outbox_before
    assert reprocess_success_metric() == metric_before + 2

    for dead_letter_event in (first, second):
        db_session.refresh(dead_letter_event.event)
        event = dead_letter_event.event
        assert event.status == EventStatus.QUEUED.value
        assert event.routing_key == "analytics.page_viewed"
        assert event.correlation_id == "correlation-123"
        assert event.trace_id == "trace-456"
        outbox_message = outbox_for(db_session, event.id)
        assert outbox_message.status == OutboxStatus.PENDING.value
        assert outbox_message.attempt_count == 0
        assert outbox_message.published_at is None
        assert outbox_message.routing_key == "analytics.page_viewed"
        assert outbox_message.headers == {"x-reprocess-dead-letter-event-id": dead_letter_event.id}
        assert outbox_message.payload == {
            "event_id": event.id,
            "event_type": "analytics.page_viewed",
            "payload": {"page": "/pricing"},
            "routing_key": "analytics.page_viewed",
            "correlation_id": "correlation-123",
            "trace_id": "trace-456",
        }
        state = db_session.scalar(
            select(EventProcessingState).where(EventProcessingState.event_id == event.id)
        )
        assert state is not None
        assert state.status == EventProcessingStatus.PENDING.value
        assert reprocess_log_count(db_session, event.id) == 1

    db_session.refresh(processed.event)
    assert processed.event.status == EventStatus.PROCESSED.value
    assert outbox_for(db_session, processed.event_id).status == OutboxStatus.PUBLISHED.value


def test_batch_reprocess_repeated_request_does_not_queue_again(
    client: TestClient,
    db_session: Session,
) -> None:
    dead_letter_events = [seed_dead_letter_event(db_session) for _ in range(2)]
    request = {"dead_letter_event_ids": [dead_letter_event.id for dead_letter_event in dead_letter_events]}

    first_response = client.post(BATCH_URL, json=request)
    outbox_message = acquire_next_outbox_message(db_session, "publisher-a")
    assert outbox_message is not None
    db_session.commit()
    second_response = client.post(BATCH_URL, json=request)

    assert first_response.json()["summary"]["queued"] == 2
    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert second_payload["summary"] == {"total": 2, "queued": 0, "conflict": 2, "not_found": 0, "failed": 0}
    assert {item["reason"] for item in second_payload["results"]} == {"not_dead_letter"}

    statuses = sorted(
        outbox_for(db_session, dead_letter_event.event_id).status for dead_letter_event in dead_letter_events
    )
    assert statuses == [OutboxStatus.PENDING.value, OutboxStatus.PUBLISHING.value]
    for dead_letter_event in dead_letter_events:
        assert reprocess_log_count(db_session, dead_letter_event.event_id) == 1


def test_batch_reprocess_queues_event_once_for_multiple_dead_letter_entries(
    client: TestClient,
    db_session: Session,
) -> None:
    first_entry = seed_dead_letter_event(db_session)
    second_entry = seed_extra_dead_letter_entry(db_session, first_entry.event_id)

    response = client.post(
        BATCH_URL,
        json={"dead_letter_event_ids": [first_entry.id, second_entry.id]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {"total": 2, "queued": 1, "conflict": 1, "not_found": 0, "failed": 0}
    assert payload["results"][1]["status"] == "conflict"
    assert payload["results"][1]["reason"] == "event_already_queued_in_batch"
    assert payload["results"][1]["event_id"] == first_entry.event_id

    outbox_messages = list(
        db_session.scalars(select(OutboxMessage).where(OutboxMessage.event_id == first_entry.event_id)).all()
    )
    assert len(outbox_messages) == 1
    assert outbox_messages[0].headers == {"x-reprocess-dead-letter-event-id": first_entry.id}
    assert reprocess_log_count(db_session, first_entry.event_id) == 1


def test_batch_reprocess_rolls_back_failed_item_and_continues(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing = seed_dead_letter_event(db_session)
    healthy = seed_dead_letter_event(db_session)
    failing_id, failing_event_id = failing.id, failing.event_id
    original_queue_outbox = DeadLetterService._queue_outbox_reprocess

    def flaky_queue_outbox(self, dead_letter_event, event, routing_key) -> None:
        if dead_letter_event.id == failing_id:
            raise RuntimeError("database write failed")
        original_queue_outbox(self, dead_letter_event, event, routing_key)

    monkeypatch.setattr(DeadLetterService, "_queue_outbox_reprocess", flaky_queue_outbox)
    metric_before = reprocess_success_metric()

    response = client.post(BATCH_URL, json={"dead_letter_event_ids": [failing_id, healthy.id]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {"total": 2, "queued": 1, "conflict": 0, "not_found": 0, "failed": 1}
    assert payload["results"][0]["status"] == "failed"
    assert payload["results"][0]["reason"] == "internal_error"
    assert "database write failed" not in payload["results"][0]["message"]
    assert payload["results"][1]["status"] == "queued"
    assert reprocess_success_metric() == metric_before + 1

    db_session.expire_all()
    failed_event = db_session.get(Event, failing_event_id)
    failed_state = db_session.scalar(
        select(EventProcessingState).where(EventProcessingState.event_id == failing_event_id)
    )
    assert failed_event is not None
    assert failed_event.status == EventStatus.DEAD_LETTER.value
    assert failed_state is not None
    assert failed_state.status == EventProcessingStatus.DEAD_LETTERED.value
    assert failed_state.worker_name == "relay-audit-worker"
    assert outbox_for(db_session, failing_event_id).status == OutboxStatus.PUBLISHED.value
    assert reprocess_log_count(db_session, failing_event_id) == 0
    assert outbox_for(db_session, healthy.event_id).status == OutboxStatus.PENDING.value


def test_batch_reprocess_commit_failure_is_not_reported_or_counted_as_success(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)
    dead_letter_event_id, event_id = dead_letter_event.id, dead_letter_event.event_id
    metric_before = reprocess_success_metric()

    def failing_commit() -> None:
        raise RuntimeError("commit failed")

    with monkeypatch.context() as patched:
        patched.setattr(db_session, "commit", failing_commit)
        response = client.post(BATCH_URL, json={"dead_letter_event_ids": [dead_letter_event_id]})

    assert response.status_code == 200
    assert response.json()["summary"] == {"total": 1, "queued": 0, "conflict": 0, "not_found": 0, "failed": 1}
    assert reprocess_success_metric() == metric_before
    db_session.expire_all()
    event = db_session.get(Event, event_id)
    assert event is not None
    assert event.status == EventStatus.DEAD_LETTER.value
    assert outbox_for(db_session, event_id).status == OutboxStatus.PUBLISHED.value
    assert reprocess_log_count(db_session, event_id) == 0


def test_single_reprocess_commit_failure_rolls_back_without_success_metric(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)
    dead_letter_event_id, event_id = dead_letter_event.id, dead_letter_event.event_id
    metric_before = reprocess_success_metric()

    def failing_commit() -> None:
        raise RuntimeError("commit failed")

    with monkeypatch.context() as patched:
        patched.setattr(db_session, "commit", failing_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            DeadLetterService(db_session).reprocess_dead_letter_event(dead_letter_event_id)

    assert reprocess_success_metric() == metric_before
    db_session.expire_all()
    event = db_session.get(Event, event_id)
    assert event is not None
    assert event.status == EventStatus.DEAD_LETTER.value
    assert outbox_for(db_session, event_id).status == OutboxStatus.PUBLISHED.value


class FakeDriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("driver detail that must stay internal")
        self.sqlstate = sqlstate


def reprocess_failure_metric(reason: str) -> float:
    return REGISTRY.get_sample_value(
        "relay_dead_letter_reprocess_failures_total",
        {"routing_key": "unknown", "reason": reason},
    ) or 0.0


def raise_database_error(monkeypatch: pytest.MonkeyPatch, sqlstate: str) -> None:
    def failing_queue_outbox(self, dead_letter_event, event, routing_key) -> None:
        raise OperationalError("select 1", {}, FakeDriverError(sqlstate))

    monkeypatch.setattr(DeadLetterService, "_queue_outbox_reprocess", failing_queue_outbox)


@pytest.mark.parametrize(
    ("sqlstate", "expected_reason"),
    [
        ("55P03", "lock_unavailable"),
        ("40P01", "lock_unavailable"),
        ("57014", "internal_error"),
        ("08006", "internal_error"),
    ],
)
def test_batch_reprocess_classifies_only_lock_errors_as_lock_unavailable(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    sqlstate: str,
    expected_reason: str,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)
    dead_letter_event_id, event_id = dead_letter_event.id, dead_letter_event.event_id
    raise_database_error(monkeypatch, sqlstate)
    metric_before = reprocess_failure_metric(expected_reason)

    response = client.post(BATCH_URL, json={"dead_letter_event_ids": [dead_letter_event_id]})

    assert response.status_code == 200
    item = response.json()["results"][0]
    assert item["status"] == "failed"
    assert item["reason"] == expected_reason
    assert "driver detail" not in item["message"]
    assert reprocess_failure_metric(expected_reason) == metric_before + 1
    db_session.expire_all()
    event = db_session.get(Event, event_id)
    assert event is not None
    assert event.status == EventStatus.DEAD_LETTER.value
    assert reprocess_log_count(db_session, event_id) == 0


def test_single_reprocess_lock_error_returns_503_without_database_details(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)
    dead_letter_event_id, event_id = dead_letter_event.id, dead_letter_event.event_id
    raise_database_error(monkeypatch, "55P03")
    metric_before = reprocess_success_metric()

    response = client.post(f"/api/dead-letter-events/{dead_letter_event_id}/reprocess")

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert "driver detail" not in response.text
    assert reprocess_success_metric() == metric_before
    db_session.expire_all()
    event = db_session.get(Event, event_id)
    assert event is not None
    assert event.status == EventStatus.DEAD_LETTER.value


def test_single_reprocess_does_not_mask_unexpected_database_error_as_lock_error(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead_letter_event = seed_dead_letter_event(db_session)
    dead_letter_event_id = dead_letter_event.id
    raise_database_error(monkeypatch, "57014")

    with pytest.raises(OperationalError) as raised:
        DeadLetterService(db_session).reprocess_dead_letter_event(dead_letter_event_id)

    assert not isinstance(raised.value, ReprocessLockUnavailableError)
