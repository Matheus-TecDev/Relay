from time import perf_counter

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import EventLogLevel, EventStatus
from app.models.event import Event
from app.models.event_log import EventLog
from app.observability.metrics import (
    observe_event_creation_duration,
    record_event_created,
    record_event_publish_failed,
    record_event_published,
)
from app.observability.tracing import generate_correlation_id, generate_trace_id
from app.queues.rabbitmq import publish_event
from app.schemas.event import EventCreate


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
        self._log(
            event.id,
            EventLogLevel.INFO,
            "Event received",
            {"event_type": event.event_type, "correlation_id": correlation_id, "trace_id": trace_id},
        )
        self.db.commit()
        self.db.refresh(event)

        try:
            publish_event(
                {
                    "event_id": event.id,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "routing_key": event.routing_key,
                    "correlation_id": event.correlation_id,
                    "trace_id": event.trace_id,
                },
                routing_key=routing_key,
            )
        except Exception as exc:
            event.status = EventStatus.PUBLISH_FAILED.value
            self._log(event.id, EventLogLevel.ERROR, "Failed to publish event", {"error": str(exc)})
            record_event_publish_failed(event.event_type, routing_key)
            observe_event_creation_duration(event.event_type, routing_key, perf_counter() - started_at)
            self.db.commit()
            raise EventPublishError from exc

        event.status = EventStatus.QUEUED.value
        self._log(
            event.id,
            EventLogLevel.INFO,
            "Event published to exchange",
            {"exchange": settings.rabbitmq_exchange, "routing_key": routing_key},
        )
        record_event_published(event.event_type, routing_key)
        observe_event_creation_duration(event.event_type, routing_key, perf_counter() - started_at)
        self.db.commit()
        self.db.refresh(event)
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
