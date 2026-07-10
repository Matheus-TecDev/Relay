from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_current_user
from app.db.session import get_db
from app.schemas.dead_letter_event import (
    DeadLetterEventDetail,
    DeadLetterEventListItem,
    DeadLetterReprocessResponse,
)
from app.services.dead_letter_service import (
    DeadLetterEventNotFoundError,
    DeadLetterPublishError,
    DeadLetterService,
    UnsafeReprocessError,
)

router = APIRouter(prefix="/dead-letter-events", tags=["dead-letter-events"], dependencies=[Depends(require_current_user)])


def _to_list_item(dead_letter_event) -> DeadLetterEventListItem:
    event = dead_letter_event.event
    return DeadLetterEventListItem(
        id=dead_letter_event.id,
        event_id=dead_letter_event.event_id,
        reason=dead_letter_event.reason,
        error_message=dead_letter_event.error_message,
        retry_count=dead_letter_event.retry_count,
        original_routing_key=dead_letter_event.original_routing_key,
        created_at=dead_letter_event.created_at,
        correlation_id=event.correlation_id,
        trace_id=event.trace_id,
        event_status=event.status,
    )


@router.get("", response_model=list[DeadLetterEventListItem])
def list_dead_letter_events(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[DeadLetterEventListItem]:
    service = DeadLetterService(db)
    return [_to_list_item(dead_letter_event) for dead_letter_event in service.list_dead_letter_events(limit)]


@router.get("/{dead_letter_event_id}", response_model=DeadLetterEventDetail)
def get_dead_letter_event(
    dead_letter_event_id: str,
    db: Session = Depends(get_db),
) -> DeadLetterEventDetail:
    service = DeadLetterService(db)
    try:
        dead_letter_event = service.get_dead_letter_event(dead_letter_event_id)
    except DeadLetterEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dead letter event not found.") from exc

    return DeadLetterEventDetail(
        id=dead_letter_event.id,
        event_id=dead_letter_event.event_id,
        reason=dead_letter_event.reason,
        payload=dead_letter_event.payload,
        retry_count=dead_letter_event.retry_count,
        original_routing_key=dead_letter_event.original_routing_key,
        error_message=dead_letter_event.error_message,
        created_at=dead_letter_event.created_at,
        event=dead_letter_event.event,
        attempts=sorted(dead_letter_event.event.attempts, key=lambda attempt: attempt.attempt_number),
        logs=sorted(dead_letter_event.event.logs, key=lambda log: log.created_at),
    )


@router.post("/{dead_letter_event_id}/reprocess", response_model=DeadLetterReprocessResponse)
def reprocess_dead_letter_event(
    dead_letter_event_id: str,
    db: Session = Depends(get_db),
) -> DeadLetterReprocessResponse:
    service = DeadLetterService(db)
    try:
        dead_letter_event = service.reprocess_dead_letter_event(dead_letter_event_id)
    except DeadLetterEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dead letter event not found.") from exc
    except UnsafeReprocessError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DeadLetterPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dead letter event was not republished.",
        ) from exc

    event = dead_letter_event.event
    routing_key = dead_letter_event.original_routing_key or event.routing_key or ""
    return DeadLetterReprocessResponse(
        dead_letter_event_id=dead_letter_event.id,
        event_id=event.id,
        status=event.status,
        routing_key=routing_key,
        correlation_id=event.correlation_id,
        trace_id=event.trace_id,
    )
