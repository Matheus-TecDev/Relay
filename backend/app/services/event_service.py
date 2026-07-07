import logging
from time import perf_counter

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.enums import EventLogLevel, EventProcessingStatus, EventStatus
from app.models.event import Event
from app.models.event_log import EventLog
from app.models.event_processing_state import EventProcessingState
from app.observability.metrics import (
    observe_event_creation_duration,
    record_event_created,
)
from app.observability.tracing import generate_correlation_id, generate_trace_id
from app.schemas.event import EventCreate
from app.services.outbox_service import create_outbox_message_for_event

logger = logging.getLogger(__name__)


class EventPublishError(Exception):
    pass


class EventService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_event(self, payload: EventCreate) -> Event:
        started_at = perf_counter()
        routing_key = payload.routing_key or "events.created"
        correlation_id = payload.correlation_id or generate_correlation_id()
        trace_id = payload.trace_id or generate_trace_id()
        record_event_created(payload.event_type, routing_key)
        event = Event(
            event_type=payload.event_type,
            payload=payload.payload,
            routing_key=routing_key,
            correlation_id=correlation_id,
            trace_id=trace_id,
            status=EventStatus.RECEIVED.value,
        )
        self.db.add(event)
        self.db.flush()
        self.db.add(
            EventProcessingState(
                event_id=event.id,
                status=EventProcessingStatus.PENDING.value,
                routing_key=routing_key,
            )
        )
        outbox_message = create_outbox_message_for_event(event)
        self.db.add(outbox_message)
        self.db.flush()
        self._log(
            event.id,
            EventLogLevel.INFO,
            "Event received",
            {"event_type": event.event_type, "correlation_id": correlation_id, "trace_id": trace_id},
        )
        self._log(
            event.id,
            EventLogLevel.INFO,
            "Event queued in outbox",
            {
                "outbox_message_id": outbox_message.id,
                "exchange": outbox_message.exchange,
                "routing_key": outbox_message.routing_key,
            },
        )
        observe_event_creation_duration(event.event_type, routing_key, perf_counter() - started_at)
        self.db.commit()
        self.db.refresh(event)
        logger.info(
            "Event queued in outbox",
            extra={
                "event_id": event.id,
                "outbox_message_id": outbox_message.id,
                "event_type": event.event_type,
                "routing_key": routing_key,
                "correlation_id": event.correlation_id,
                "trace_id": event.trace_id,
            },
        )
        return event

    def list_events(self, limit: int = 25) -> list[Event]:
        statement = select(Event).order_by(desc(Event.created_at)).limit(limit)
        return list(self.db.scalars(statement).all())

    def _log(self, event_id: str, level: EventLogLevel, message: str, metadata: dict) -> None:
        self.db.add(
            EventLog(
                event_id=event_id,
                level=level.value,
                message=message,
                log_metadata=metadata,
            )
        )
