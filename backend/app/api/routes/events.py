from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_current_user
from app.db.session import get_db
from app.schemas.event import EventCreate, EventDetail, EventRead, EventSummary
from app.services.event_service import EventNotFoundError, EventPublishError, EventService

router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(require_current_user)])


@router.post("", response_model=EventRead, status_code=status.HTTP_201_CREATED)
def create_event(payload: EventCreate, db: Session = Depends(get_db)) -> EventRead:
    service = EventService(db)
    try:
        return service.create_event(payload)
    except EventPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Event was stored, but RabbitMQ publishing failed.",
        ) from exc


@router.get("", response_model=list[EventRead])
def list_events(
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[EventRead]:
    service = EventService(db)
    return service.list_events(limit=limit)


@router.get("/summary", response_model=EventSummary)
def get_events_summary(db: Session = Depends(get_db)) -> EventSummary:
    service = EventService(db)
    return service.get_summary()


@router.get("/{event_id}", response_model=EventDetail)
def get_event(event_id: str, db: Session = Depends(get_db)) -> EventDetail:
    service = EventService(db)
    try:
        event = service.get_event(event_id)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.") from exc

    return EventDetail(
        id=event.id,
        event_type=event.event_type,
        payload=event.payload,
        routing_key=event.routing_key,
        correlation_id=event.correlation_id,
        trace_id=event.trace_id,
        status=event.status,
        created_at=event.created_at,
        updated_at=event.updated_at,
        attempts=sorted(event.attempts, key=lambda attempt: attempt.attempt_number),
        logs=sorted(event.logs, key=lambda log: log.created_at),
        dead_letter_entries=sorted(event.dead_letter_entries, key=lambda entry: entry.created_at),
    )
