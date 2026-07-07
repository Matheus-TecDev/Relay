import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import EventLogLevel, EventStatus, OutboxStatus
from app.models.event import Event
from app.models.event_log import EventLog
from app.models.outbox_message import OutboxMessage
from app.observability.metrics import (
    observe_event_creation_duration,
    record_event_publish_failed,
    record_event_published,
    record_outbox_message_published,
    record_outbox_publish_failed,
    record_outbox_retry,
)
from app.queues.rabbitmq import publish_message

logger = logging.getLogger(__name__)


def backoff_delay_for_attempt(attempt_count: int) -> int:
    delays = [
        int(raw_delay.strip())
        for raw_delay in settings.outbox_backoff_seconds.split(",")
        if raw_delay.strip()
    ]
    if not delays:
        return 60

    index = max(attempt_count - 1, 0)
    if index >= len(delays):
        return delays[-1]
    return delays[index]


def create_outbox_message_for_event(event: Event) -> OutboxMessage:
    return OutboxMessage(
        event_id=event.id,
        exchange=settings.rabbitmq_exchange,
        routing_key=event.routing_key or "events.created",
        payload={
            "event_id": event.id,
            "event_type": event.event_type,
            "payload": event.payload,
            "routing_key": event.routing_key,
            "correlation_id": event.correlation_id,
            "trace_id": event.trace_id,
        },
        headers={},
        status=OutboxStatus.PENDING.value,
    )


def acquire_next_outbox_message(
    db: Session,
    publisher_name: str,
) -> OutboxMessage | None:
    recover_stuck_publishing_messages(db)
    now = datetime.now(UTC)
    statement = (
        select(OutboxMessage)
        .where(OutboxMessage.status.in_([OutboxStatus.PENDING.value, OutboxStatus.FAILED.value]))
        .where(or_(OutboxMessage.next_attempt_at.is_(None), OutboxMessage.next_attempt_at <= now))
        .order_by(OutboxMessage.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    outbox_message = db.scalar(statement)
    if outbox_message is None:
        return None

    outbox_message.status = OutboxStatus.PUBLISHING.value
    outbox_message.locked_at = now
    outbox_message.locked_by = publisher_name
    outbox_message.last_attempt_at = now
    outbox_message.attempt_count += 1
    db.flush()
    return outbox_message


def publish_outbox_message(
    db: Session,
    outbox_message: OutboxMessage,
    publisher_name: str,
) -> None:
    event = db.get(Event, outbox_message.event_id)
    started_at = datetime.now(UTC)
    try:
        publish_message(
            outbox_message.exchange,
            outbox_message.routing_key,
            outbox_message.payload,
            headers=outbox_message.headers,
        )
    except Exception as exc:
        mark_outbox_failed(db, outbox_message, str(exc))
        record_event_publish_failed(
            outbox_message.payload.get("event_type", "unknown"),
            outbox_message.routing_key,
        )
        record_outbox_publish_failed(outbox_message.routing_key)
        record_outbox_retry(outbox_message.routing_key, outbox_message.attempt_count)
        if event is not None:
            event.status = EventStatus.PUBLISH_FAILED.value
            db.add(
                EventLog(
                    event_id=event.id,
                    level=EventLogLevel.ERROR.value,
                    message="Outbox publish failed",
                    log_metadata={
                        "outbox_message_id": outbox_message.id,
                        "attempt_count": outbox_message.attempt_count,
                        "routing_key": outbox_message.routing_key,
                        "error": str(exc),
                        "publisher_name": publisher_name,
                    },
                )
            )
        logger.exception(
            "Outbox publish failed",
            extra=_log_extra(outbox_message, event, publisher_name, OutboxStatus.FAILED.value),
        )
        db.commit()
        return

    outbox_message.status = OutboxStatus.PUBLISHED.value
    outbox_message.published_at = datetime.now(UTC)
    outbox_message.last_error = None
    outbox_message.next_attempt_at = None
    outbox_message.locked_at = None
    outbox_message.locked_by = None
    if event is not None:
        event.status = EventStatus.QUEUED.value
        db.add(
            EventLog(
                event_id=event.id,
                level=EventLogLevel.INFO.value,
                message="Event published to exchange from outbox",
                log_metadata={
                    "outbox_message_id": outbox_message.id,
                    "exchange": outbox_message.exchange,
                    "routing_key": outbox_message.routing_key,
                    "publisher_name": publisher_name,
                },
            )
        )
        record_event_published(event.event_type, outbox_message.routing_key)
        observe_event_creation_duration(
            event.event_type,
            outbox_message.routing_key,
            max((datetime.now(UTC) - started_at).total_seconds(), 0),
        )
    record_outbox_message_published(outbox_message.routing_key)
    logger.info(
        "Outbox message published",
        extra=_log_extra(outbox_message, event, publisher_name, OutboxStatus.PUBLISHED.value),
    )
    db.commit()


def mark_outbox_failed(db: Session, outbox_message: OutboxMessage, error_message: str) -> None:
    delay_seconds = backoff_delay_for_attempt(outbox_message.attempt_count)
    outbox_message.status = OutboxStatus.FAILED.value
    outbox_message.last_error = error_message
    outbox_message.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
    outbox_message.locked_at = None
    outbox_message.locked_by = None
    db.flush()


def recover_stuck_publishing_messages(db: Session) -> list[OutboxMessage]:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.outbox_publishing_timeout_seconds)
    stuck_messages = list(
        db.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.status == OutboxStatus.PUBLISHING.value)
            .where(OutboxMessage.locked_at < cutoff)
            .with_for_update(skip_locked=True)
        ).all()
    )
    for outbox_message in stuck_messages:
        locked_by = outbox_message.locked_by or "unknown"
        event = db.get(Event, outbox_message.event_id)
        outbox_message.status = OutboxStatus.FAILED.value
        outbox_message.last_error = "publishing_timeout"
        outbox_message.next_attempt_at = datetime.now(UTC)
        outbox_message.locked_at = None
        outbox_message.locked_by = None
        logger.warning(
            "Recovered stuck outbox message",
            extra=_log_extra(outbox_message, event, locked_by, OutboxStatus.FAILED.value),
        )
    if stuck_messages:
        db.flush()
    return stuck_messages


def _log_extra(
    outbox_message: OutboxMessage,
    event: Event | None,
    publisher_name: str,
    status: str,
) -> dict:
    payload = outbox_message.payload or {}
    return {
        "outbox_message_id": outbox_message.id,
        "event_id": outbox_message.event_id,
        "event_type": payload.get("event_type"),
        "routing_key": outbox_message.routing_key,
        "status": status,
        "attempt_count": outbox_message.attempt_count,
        "publisher_name": publisher_name,
        "correlation_id": payload.get("correlation_id") or (event.correlation_id if event else None),
        "trace_id": payload.get("trace_id") or (event.trace_id if event else None),
    }
