from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import EventProcessingStatus
from app.models.event import Event
from app.models.event_processing_state import EventProcessingState


class IdempotencyDecision(StrEnum):
    ACQUIRED = "acquired"
    DUPLICATE_PROCESSED = "duplicate_processed"
    ALREADY_PROCESSING = "already_processing"
    TERMINAL_DEAD_LETTERED = "terminal_dead_lettered"


@dataclass(frozen=True)
class IdempotencyResult:
    decision: IdempotencyDecision
    state: EventProcessingState


def ensure_pending_processing_state(db: Session, event: Event) -> EventProcessingState:
    state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event.id))
    if state is not None:
        return state

    state = EventProcessingState(
        event_id=event.id,
        status=EventProcessingStatus.PENDING.value,
        routing_key=event.routing_key,
    )
    db.add(state)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        state = db.scalar(select(EventProcessingState).where(EventProcessingState.event_id == event.id))
        if state is None:
            raise
    return state


def acquire_processing_state(
    db: Session,
    event: Event,
    worker_name: str,
    routing_key: str,
) -> IdempotencyResult:
    ensure_pending_processing_state(db, event)
    state = db.scalar(
        select(EventProcessingState)
        .where(EventProcessingState.event_id == event.id)
        .with_for_update()
    )
    if state is None:
        raise RuntimeError(f"Processing state not found for event_id={event.id}")

    if state.status == EventProcessingStatus.PROCESSED.value:
        return IdempotencyResult(IdempotencyDecision.DUPLICATE_PROCESSED, state)

    if state.status == EventProcessingStatus.DEAD_LETTERED.value:
        return IdempotencyResult(IdempotencyDecision.TERMINAL_DEAD_LETTERED, state)

    if state.status == EventProcessingStatus.PROCESSING.value and not _processing_state_is_stale(state):
        return IdempotencyResult(IdempotencyDecision.ALREADY_PROCESSING, state)

    state.status = EventProcessingStatus.PROCESSING.value
    state.worker_name = worker_name
    state.routing_key = routing_key
    state.processing_started_at = datetime.now(UTC)
    state.processed_at = None
    state.failed_at = None
    state.dead_lettered_at = None
    state.error_message = None
    state.attempt_count += 1
    db.flush()
    return IdempotencyResult(IdempotencyDecision.ACQUIRED, state)


def mark_processing_processed(db: Session, event_id: str) -> None:
    state = _locked_state(db, event_id)
    state.status = EventProcessingStatus.PROCESSED.value
    state.processed_at = datetime.now(UTC)
    state.error_message = None
    db.flush()


def mark_processing_failed(db: Session, event_id: str, error_message: str) -> None:
    state = _locked_state(db, event_id)
    state.status = EventProcessingStatus.FAILED.value
    state.failed_at = datetime.now(UTC)
    state.error_message = error_message
    db.flush()


def mark_processing_dead_lettered(db: Session, event_id: str, error_message: str) -> None:
    state = _locked_state(db, event_id)
    state.status = EventProcessingStatus.DEAD_LETTERED.value
    state.dead_lettered_at = datetime.now(UTC)
    state.error_message = error_message
    db.flush()


def stuck_processing_events(db: Session) -> list[EventProcessingState]:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.idempotency_processing_timeout_seconds)
    return list(
        db.scalars(
            select(EventProcessingState)
            .where(EventProcessingState.status == EventProcessingStatus.PROCESSING.value)
            .where(EventProcessingState.processing_started_at < cutoff)
        ).all()
    )


def _locked_state(db: Session, event_id: str) -> EventProcessingState:
    state = db.scalar(
        select(EventProcessingState)
        .where(EventProcessingState.event_id == event_id)
        .with_for_update()
    )
    if state is None:
        raise RuntimeError(f"Processing state not found for event_id={event_id}")
    return state


def _processing_state_is_stale(state: EventProcessingState) -> bool:
    if state.processing_started_at is None:
        return True

    processing_started_at = state.processing_started_at
    if processing_started_at.tzinfo is None:
        processing_started_at = processing_started_at.replace(tzinfo=UTC)

    timeout = timedelta(seconds=settings.idempotency_processing_timeout_seconds)
    return datetime.now(UTC) - processing_started_at > timeout
