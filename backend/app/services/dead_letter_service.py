import logging
from dataclasses import dataclass

from sqlalchemy import desc, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.enums import (
    DeadLetterReprocessItemStatus,
    EventLogLevel,
    EventProcessingStatus,
    EventStatus,
    OutboxStatus,
)
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

LOCK_UNAVAILABLE_SQLSTATES = frozenset({"55P03", "40P01"})


class DeadLetterEventNotFoundError(Exception):
    pass


class UnsafeReprocessError(Exception):
    def __init__(self, message: str, reason: str, event_id: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.event_id = event_id


class ReprocessLockUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class QueuedReprocess:
    dead_letter_event_id: str
    event_id: str
    routing_key: str
    correlation_id: str
    trace_id: str


@dataclass(frozen=True)
class BatchReprocessItemResult:
    dead_letter_event_id: str
    status: DeadLetterReprocessItemStatus
    reason: str | None = None
    message: str | None = None
    event_id: str | None = None
    routing_key: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = None


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
        try:
            dead_letter_event, queued = self._queue_reprocess(dead_letter_event_id)
            self.db.commit()
        except DBAPIError as exc:
            self.db.rollback()
            if not self._is_lock_unavailable(exc):
                raise
            self._record_lock_unavailable(dead_letter_event_id, exc)
            raise ReprocessLockUnavailableError from exc
        except Exception:
            self.db.rollback()
            raise

        self._record_reprocess_queued(queued)
        self.db.refresh(dead_letter_event)
        return dead_letter_event

    def reprocess_dead_letter_events(
        self,
        dead_letter_event_ids: list[str],
    ) -> list[BatchReprocessItemResult]:
        results: list[BatchReprocessItemResult] = []
        queued_event_ids: set[str] = set()

        for dead_letter_event_id in dead_letter_event_ids:
            result = self._reprocess_batch_item(dead_letter_event_id, queued_event_ids)
            if result.status == DeadLetterReprocessItemStatus.QUEUED and result.event_id is not None:
                queued_event_ids.add(result.event_id)
            results.append(result)

        logger.info(
            "Manual DLQ batch reprocess finished",
            extra={
                "batch_size": len(results),
                **{
                    f"batch_{item_status.value}": sum(1 for result in results if result.status == item_status)
                    for item_status in DeadLetterReprocessItemStatus
                },
            },
        )
        return results

    def _reprocess_batch_item(
        self,
        dead_letter_event_id: str,
        queued_event_ids: set[str],
    ) -> BatchReprocessItemResult:
        try:
            _, queued = self._queue_reprocess(dead_letter_event_id)
            self.db.commit()
        except DeadLetterEventNotFoundError:
            self.db.rollback()
            return BatchReprocessItemResult(
                dead_letter_event_id=dead_letter_event_id,
                status=DeadLetterReprocessItemStatus.NOT_FOUND,
                reason="dead_letter_event_not_found",
                message="Dead letter event not found.",
            )
        except UnsafeReprocessError as exc:
            self.db.rollback()
            if exc.event_id is not None and exc.event_id in queued_event_ids:
                return BatchReprocessItemResult(
                    dead_letter_event_id=dead_letter_event_id,
                    status=DeadLetterReprocessItemStatus.CONFLICT,
                    reason="event_already_queued_in_batch",
                    message="Another dead letter entry in this batch already queued the same event.",
                    event_id=exc.event_id,
                )
            return BatchReprocessItemResult(
                dead_letter_event_id=dead_letter_event_id,
                status=DeadLetterReprocessItemStatus.CONFLICT,
                reason=exc.reason,
                message=str(exc),
                event_id=exc.event_id,
            )
        except Exception as exc:
            self.db.rollback()
            if self._is_lock_unavailable(exc):
                self._record_lock_unavailable(dead_letter_event_id, exc)
                return BatchReprocessItemResult(
                    dead_letter_event_id=dead_letter_event_id,
                    status=DeadLetterReprocessItemStatus.FAILED,
                    reason="lock_unavailable",
                    message="Event is locked by another operation. Reprocess was rolled back. It is safe to retry.",
                )
            record_dead_letter_reprocess_failed("unknown", "internal_error")
            logger.exception(
                "Manual DLQ reprocess failed and was rolled back",
                extra={"dead_letter_event_id": dead_letter_event_id},
            )
            return BatchReprocessItemResult(
                dead_letter_event_id=dead_letter_event_id,
                status=DeadLetterReprocessItemStatus.FAILED,
                reason="internal_error",
                message="Reprocess failed and was rolled back. It is safe to retry.",
            )

        self._record_reprocess_queued(queued)
        return BatchReprocessItemResult(
            dead_letter_event_id=dead_letter_event_id,
            status=DeadLetterReprocessItemStatus.QUEUED,
            event_id=queued.event_id,
            routing_key=queued.routing_key,
            correlation_id=queued.correlation_id,
            trace_id=queued.trace_id,
        )

    def _queue_reprocess(self, dead_letter_event_id: str) -> tuple[DeadLetterEvent, QueuedReprocess]:
        self._apply_lock_timeout()
        dead_letter_event = self._get_dead_letter_event_for_reprocess(dead_letter_event_id)
        event = self._get_event_for_reprocess(dead_letter_event.event_id)
        if event is None:
            raise DeadLetterEventNotFoundError

        routing_key = dead_letter_event.original_routing_key or event.routing_key
        if not routing_key:
            record_dead_letter_reprocess_failed("unknown", "missing_routing_key")
            raise UnsafeReprocessError(
                "Dead letter event does not have a routing key.",
                "missing_routing_key",
                event.id,
            )

        if event.status != EventStatus.DEAD_LETTER.value:
            record_dead_letter_reprocess_failed(routing_key, "not_dead_letter")
            raise UnsafeReprocessError(
                "Event is not currently in dead letter state.",
                "not_dead_letter",
                event.id,
            )

        self._reset_processing_state(event.id, routing_key)
        event.status = EventStatus.QUEUED.value
        event.routing_key = routing_key
        self._queue_outbox_reprocess(dead_letter_event, event, routing_key)

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
        return dead_letter_event, QueuedReprocess(
            dead_letter_event_id=dead_letter_event.id,
            event_id=event.id,
            routing_key=routing_key,
            correlation_id=event.correlation_id,
            trace_id=event.trace_id,
        )

    def _record_reprocess_queued(self, queued: QueuedReprocess) -> None:
        record_dead_letter_reprocess(queued.routing_key)
        logger.info(
            "Manual DLQ reprocess queued in outbox",
            extra={
                "event_id": queued.event_id,
                "dead_letter_event_id": queued.dead_letter_event_id,
                "routing_key": queued.routing_key,
                "correlation_id": queued.correlation_id,
                "trace_id": queued.trace_id,
            },
        )

    def _apply_lock_timeout(self) -> None:
        if self.db.get_bind().dialect.name != "postgresql":
            return
        self.db.execute(
            select(
                func.set_config(
                    "lock_timeout",
                    f"{settings.dead_letter_reprocess_lock_timeout_ms}ms",
                    True,
                )
            )
        )

    @staticmethod
    def _is_lock_unavailable(exc: Exception) -> bool:
        if not isinstance(exc, DBAPIError):
            return False
        return getattr(exc.orig, "sqlstate", None) in LOCK_UNAVAILABLE_SQLSTATES

    def _record_lock_unavailable(self, dead_letter_event_id: str, exc: DBAPIError) -> None:
        record_dead_letter_reprocess_failed("unknown", "lock_unavailable")
        logger.warning(
            "Manual DLQ reprocess could not acquire row locks and was rolled back",
            extra={
                "dead_letter_event_id": dead_letter_event_id,
                "sqlstate": getattr(exc.orig, "sqlstate", None),
            },
        )

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
            raise UnsafeReprocessError("Event was already processed.", "already_processed", event_id)

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
