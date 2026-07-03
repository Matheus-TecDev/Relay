from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.event import EventCreate, EventRead
from app.services.event_service import EventService, EventPublishError

router = APIRouter(prefix="/events", tags=["events"])


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

