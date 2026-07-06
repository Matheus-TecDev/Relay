from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import EventLogLevel, EventStatus
from app.models.dead_letter_event import DeadLetterEvent
from app.models.event import Event
from app.models.event_log import EventLog
from app.queues.rabbitmq import publish_event


class DeadLetterEventNotFoundError(Exception):
    pass


class UnsafeReprocessError(Exception):
    pass


class DeadLetterPublishError(Exception):
    pass


class DeadLetterService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_dead_letter_events(self, limit: int = 50) -> list[DeadLetterEvent]:
        statement = (
            select(DeadLetterEvent)
            .options(selectinload(DeadLetterEvent.event))
            .order_by(desc(DeadLetterEvent.created_at))
            .limit(limit)
        )
        return list(self.db.scalars(statement).all())

    def get_dead_letter_event(self, dead_letter_event_id: str) -> DeadLetterEvent:
        dead_letter_event = self.db.scalar(
            select(DeadLetterEvent)
            .options(
                selectinload(DeadLetterEvent.event).selectinload(Event.attempts),
                selectinload(DeadLetterEvent.event).selectinload(Event.logs),
            )
            .where(DeadLetterEvent.id == dead_letter_event_id)
        )
        if dead_letter_event is None:
            raise DeadLetterEventNotFoundError

        return dead_letter_event

    def reprocess_dead_letter_event(self, dead_letter_event_id: str) -> DeadLetterEvent:
        dead_letter_event = self.get_dead_letter_event(dead_letter_event_id)
        event = dead_letter_event.event
        if event is None:
            raise DeadLetterEventNotFoundError

        self._ensure_reprocess_is_safe(event.id)

        routing_key = dead_letter_event.original_routing_key or event.routing_key
        if not routing_key:
            raise UnsafeReprocessError("Dead letter event does not have a routing key.")

        message = {
            "event_id": event.id,
            "event_type": event.event_type,
            "payload": event.payload,
            "routing_key": routing_key,
            "correlation_id": event.correlation_id,
            "trace_id": event.trace_id,
        }

        try:
            publish_event(message, routing_key)
        except Exception as exc:
            self._log(
                event.id,
                EventLogLevel.ERROR,
                "Manual DLQ reprocess publish failed",
                {
                    "dead_letter_event_id": dead_letter_event.id,
                    "routing_key": routing_key,
                    "error": str(exc),
                },
            )
            self.db.commit()
            raise DeadLetterPublishError from exc

        event.status = EventStatus.QUEUED.value
        self._log(
            event.id,
            EventLogLevel.INFO,
            "Manual DLQ reprocess requested",
            {
                "dead_letter_event_id": dead_letter_event.id,
                "routing_key": routing_key,
                "correlation_id": event.correlation_id,
                "trace_id": event.trace_id,
            },
        )
        self.db.commit()
        self.db.refresh(dead_letter_event)
        return dead_letter_event

    def _ensure_reprocess_is_safe(self, event_id: str) -> None:
        recent_cutoff = datetime.now(UTC) - timedelta(minutes=1)
        recent_reprocess = self.db.scalar(
            select(EventLog)
            .where(EventLog.event_id == event_id)
            .where(EventLog.message == "Manual DLQ reprocess requested")
            .where(EventLog.created_at >= recent_cutoff)
            .order_by(desc(EventLog.created_at))
            .limit(1)
        )
        if recent_reprocess is not None:
            raise UnsafeReprocessError("Event was manually reprocessed recently.")

    def _log(
        self,
        event_id: str,
        level: EventLogLevel,
        message: str,
        metadata: dict,
    ) -> None:
        self.db.add(
            EventLog(
                event_id=event_id,
                level=level.value,
                message=message,
                log_metadata=metadata,
            )
        )
