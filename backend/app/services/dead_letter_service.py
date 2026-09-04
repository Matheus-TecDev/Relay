import logging

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.enums import EventLogLevel, EventProcessingStatus, EventStatus, OutboxStatus
from app.models.dead_letter_event import DeadLetterEvent
from app.models.event import Event
from app.models.event_log import EventLog
from app.models.event_processing_state import EventProcessingState
from app.models.outbox_message import OutboxMessage
from app.observability.metrics import (
    record_dead_letter_reprocess,
    record_dead_letter_reprocess_failed,
)
from app.services.outbox_service import create_outbox_message_for_event

logger = logging.getLogger(__name__)


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
        dead_letter_event = self._get_dead_letter_event_for_reprocess(dead_letter_event_id)
        event = self._get_event_for_reprocess(dead_letter_event.event_id)
        if event is None:
            raise DeadLetterEventNotFoundError

        routing_key = dead_letter_event.original_routing_key or event.routing_key
        if not routing_key:
            record_dead_letter_reprocess_failed("unknown", "missing_routing_key")
            raise UnsafeReprocessError("Dead letter event does not have a routing key.")

        if event.status != EventStatus.DEAD_LETTER.value:
            record_dead_letter_reprocess_failed(routing_key, "not_dead_letter")
            raise UnsafeReprocessError("Event is not currently in dead letter state.")

        self._reset_processing_state(event.id, routing_key)
        event.status = EventStatus.QUEUED.value
        event.routing_key = routing_key
        self._queue_outbox_reprocess(dead_letter_event, event, routing_key)

        record_dead_letter_reprocess(routing_key)
        self._log(
            event.id,
            EventLogLevel.INFO,
            "Manual DLQ reprocess queued in outbox",
            {
                "dead_letter_event_id": dead_letter_event.id,
                "routing_key": routing_key,
                "correlation_id": event.correlation_id,
                "trace_id": event.trace_id,
            },
        )
        logger.info(
            "Manual DLQ reprocess queued in outbox",
            extra={
                "event_id": event.id,
                "dead_letter_event_id": dead_letter_event.id,
                "routing_key": routing_key,
                "correlation_id": event.correlation_id,
                "trace_id": event.trace_id,
            },
        )
        self.db.commit()
        self.db.refresh(dead_letter_event)
        return dead_letter_event

    def _get_dead_letter_event_for_reprocess(self, dead_letter_event_id: str) -> DeadLetterEvent:
        dead_letter_event = self.db.scalar(
            select(DeadLetterEvent)
            .where(DeadLetterEvent.id == dead_letter_event_id)
            .with_for_update()
        )
        if dead_letter_event is None:
            raise DeadLetterEventNotFoundError
        return dead_letter_event

    def _get_event_for_reprocess(self, event_id: str) -> Event | None:
        return self.db.scalar(select(Event).where(Event.id == event_id).with_for_update())

    def _reset_processing_state(self, event_id: str, routing_key: str) -> None:
        processing_state = self.db.scalar(
            select(EventProcessingState)
            .where(EventProcessingState.event_id == event_id)
            .with_for_update()
        )
        if processing_state is None:
            self.db.add(
                EventProcessingState(
                    event_id=event_id,
                    status=EventProcessingStatus.PENDING.value,
                    routing_key=routing_key,
                )
            )
            return

        if processing_state.status == EventProcessingStatus.PROCESSED.value:
            record_dead_letter_reprocess_failed(routing_key, "already_processed")
            raise UnsafeReprocessError("Event was already processed.")

        processing_state.status = EventProcessingStatus.PENDING.value
        processing_state.worker_name = None
        processing_state.routing_key = routing_key
        processing_state.processing_started_at = None
        processing_state.processed_at = None
        processing_state.failed_at = None
        processing_state.dead_lettered_at = None
        processing_state.error_message = None

    def _queue_outbox_reprocess(
        self,
        dead_letter_event: DeadLetterEvent,
        event: Event,
        routing_key: str,
    ) -> None:
        outbox_message = self.db.scalar(
            select(OutboxMessage)
            .where(OutboxMessage.event_id == event.id)
            .with_for_update()
        )
        queued_message = create_outbox_message_for_event(event)
        queued_message.headers = {
            "x-reprocess-dead-letter-event-id": dead_letter_event.id,
        }

        if outbox_message is None:
            self.db.add(queued_message)
            return

        outbox_message.exchange = settings.rabbitmq_exchange
        outbox_message.routing_key = routing_key
        outbox_message.payload = queued_message.payload
        outbox_message.headers = queued_message.headers
        outbox_message.status = OutboxStatus.PENDING.value
        outbox_message.attempt_count = 0
        outbox_message.last_error = None
        outbox_message.last_attempt_at = None
        outbox_message.next_attempt_at = None
        outbox_message.locked_at = None
        outbox_message.locked_by = None
        outbox_message.published_at = None

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
