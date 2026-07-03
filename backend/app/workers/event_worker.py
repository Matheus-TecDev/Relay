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
from app.observability.logging import configure_logging
from app.observability.metrics import record_event_processed
from app.queues.rabbitmq import declare_topology, rabbitmq_channel
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


def process_event(message: dict[str, Any], routing_key: str) -> None:
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
                message="Worker started processing",
                log_metadata={
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
                    message="Event processed",
                    log_metadata={"routing_key": routing_key},
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
                    message="Event processing failed",
                    log_metadata={"error": str(exc), "routing_key": routing_key},
                )
            )
            db.commit()
            raise


def handle_message(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    method: pika.spec.Basic.Deliver,
    properties: pika.BasicProperties,
    body: bytes,
) -> None:
    del properties
    try:
        message = json.loads(body.decode("utf-8"))
        process_event(message, method.routing_key)
        channel.basic_ack(delivery_tag=method.delivery_tag)
    except Exception:
        logger.exception("Failed to process queue message")
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


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
