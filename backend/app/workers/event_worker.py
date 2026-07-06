import json
import logging
from datetime import UTC, datetime
from typing import Any, Callable

import pika
from sqlalchemy import func, select

from app.core.config import settings
from app.core.enums import EventLogLevel, EventStatus
from app.db.session import SessionLocal
from app.models.event import Event
from app.models.event_attempt import EventAttempt
from app.models.event_log import EventLog
from app.models.dead_letter_event import DeadLetterEvent
from app.observability.logging import configure_logging
from app.observability.metrics import record_event_processed
from app.queues.rabbitmq import (
    declare_topology,
    publish_dead_letter_event,
    publish_retry_event,
    rabbitmq_channel,
    retry_limit_exceeded,
)
from app.workers import analytics_worker, audit_worker, notification_worker

logger = logging.getLogger(__name__)


HANDLERS: dict[str, Callable[[dict[str, Any]], None]] = {
    "audit": audit_worker.handle_event,
    "analytics": analytics_worker.handle_event,
    "notifications": notification_worker.handle_event,
    "events": audit_worker.handle_event,
}


def _resolve_handler(routing_key: str) -> Callable[[dict[str, Any]], None]:
    namespace = routing_key.split(".", maxsplit=1)[0]
    return HANDLERS.get(namespace, audit_worker.handle_event)


def process_event(message: dict[str, Any], routing_key: str, retry_count: int = 0) -> None:
    event_id = message["event_id"]
    handler = _resolve_handler(routing_key)

    with SessionLocal() as db:
        event = db.get(Event, event_id)
        if event is None:
            logger.warning("Received message for unknown event_id=%s", event_id)
            return

        attempt_number = (
            db.scalar(select(func.count(EventAttempt.id)).where(EventAttempt.event_id == event_id)) or 0
        ) + 1
        attempt = EventAttempt(
            event_id=event_id,
            attempt_number=attempt_number,
            status=EventStatus.PROCESSING.value,
        )
        event.status = EventStatus.PROCESSING.value
        db.add(attempt)
        db.add(
            EventLog(
                event_id=event_id,
                level=EventLogLevel.INFO.value,
                message="Event attempt started",
                log_metadata={
                    "attempt_number": attempt_number,
                    "retry_count": retry_count,
                    "routing_key": routing_key,
                    "correlation_id": event.correlation_id,
                    "trace_id": event.trace_id,
                },
            )
        )
        db.commit()

        try:
            handler(message)
            attempt.status = EventStatus.PROCESSED.value
            attempt.finished_at = datetime.now(UTC)
            event.status = EventStatus.PROCESSED.value
            db.add(
                EventLog(
                    event_id=event_id,
                    level=EventLogLevel.INFO.value,
                    message="Event attempt succeeded",
                    log_metadata={
                        "attempt_number": attempt_number,
                        "retry_count": retry_count,
                        "routing_key": routing_key,
                    },
                )
            )
            record_event_processed(event.event_type, routing_key)
            db.commit()
        except Exception as exc:
            attempt.status = EventStatus.FAILED.value
            attempt.error_message = str(exc)
            attempt.finished_at = datetime.now(UTC)
            event.status = EventStatus.FAILED.value
            db.add(
                EventLog(
                    event_id=event_id,
                    level=EventLogLevel.ERROR.value,
                    message="Event attempt failed",
                    log_metadata={
                        "attempt_number": attempt_number,
                        "retry_count": retry_count,
                        "error": str(exc),
                        "routing_key": routing_key,
                    },
                )
            )
            db.commit()
            raise


def _retry_count_from_headers(headers: dict[str, Any]) -> int:
    raw_value = headers.get("x-retry-count", 0)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 0


def _original_routing_key(headers: dict[str, Any], fallback: str) -> str:
    original_routing_key = headers.get("x-original-routing-key")
    if isinstance(original_routing_key, str) and original_routing_key:
        return original_routing_key

    return fallback


def _record_retry(
    event_id: str,
    retry_count: int,
    retry_queue: str,
    original_routing_key: str,
    error_message: str,
) -> None:
    with SessionLocal() as db:
        event = db.get(Event, event_id)
        if event is None:
            return

        event.status = EventStatus.QUEUED.value
        db.add(
            EventLog(
                event_id=event_id,
                level=EventLogLevel.WARNING.value,
                message="Event sent to retry queue",
                log_metadata={
                    "retry_count": retry_count,
                    "retry_queue": retry_queue,
                    "original_routing_key": original_routing_key,
                    "error": error_message,
                },
            )
        )
        db.commit()


def _mark_event_dead_letter(
    event_id: str,
    message: dict[str, Any],
    retry_count: int,
    original_routing_key: str,
    error_message: str,
) -> None:
    with SessionLocal() as db:
        event = db.get(Event, event_id)
        if event is None:
            return

        event.status = EventStatus.DEAD_LETTER.value
        db.add(
            DeadLetterEvent(
                event_id=event_id,
                reason="retry_limit_exceeded",
                payload=message,
                retry_count=retry_count,
                original_routing_key=original_routing_key,
                error_message=error_message,
            )
        )
        db.add(
            EventLog(
                event_id=event_id,
                level=EventLogLevel.ERROR.value,
                message="Event sent to dead letter queue",
                log_metadata={
                    "retry_count": retry_count,
                    "original_routing_key": original_routing_key,
                    "error": error_message,
                },
            )
        )
        db.commit()


def handle_message(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    method: pika.spec.Basic.Deliver,
    properties: pika.BasicProperties,
    body: bytes,
) -> None:
    headers = dict(properties.headers or {})
    retry_count = _retry_count_from_headers(headers)
    original_routing_key = _original_routing_key(headers, method.routing_key)
    message: dict[str, Any] = {}

    try:
        message = json.loads(body.decode("utf-8"))
        process_event(message, original_routing_key, retry_count)
        channel.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as exc:
        logger.exception("Failed to process queue message")
        if "event_id" not in message:
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        next_retry_count = retry_count + 1
        error_message = str(exc)

        if retry_limit_exceeded(next_retry_count):
            publish_dead_letter_event(message, original_routing_key, next_retry_count, error_message)
            _mark_event_dead_letter(
                message["event_id"],
                message,
                next_retry_count,
                original_routing_key,
                error_message,
            )
        else:
            retry_policy = publish_retry_event(
                message,
                original_routing_key,
                next_retry_count,
                error_message,
            )
            _record_retry(
                message["event_id"],
                next_retry_count,
                retry_policy.queue_name,
                original_routing_key,
                error_message,
            )

        channel.basic_ack(delivery_tag=method.delivery_tag)


def main() -> None:
    configure_logging()
    queue_name = settings.rabbitmq_audit_queue
    logger.info("Starting Relay worker. exchange=%s queue=%s", settings.rabbitmq_exchange, queue_name)
    with rabbitmq_channel() as channel:
        declare_topology(channel)
        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(queue=queue_name, on_message_callback=handle_message)
        channel.start_consuming()


if __name__ == "__main__":
    main()
