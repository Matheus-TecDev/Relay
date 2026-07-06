from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.dead_letter_event import DeadLetterEvent
from app.models.event import Event
from app.models.event_attempt import EventAttempt

EVENTS_CREATED = Counter(
    "relay_events_created_total",
    "Total events received by the API.",
    ("event_type", "routing_key"),
)
EVENTS_PUBLISHED = Counter(
    "relay_events_published_total",
    "Total events published to RabbitMQ.",
    ("event_type", "routing_key"),
)
EVENT_PUBLISH_FAILURES = Counter(
    "relay_event_publish_failures_total",
    "Total failures while publishing events to RabbitMQ.",
    ("event_type", "routing_key"),
)
EVENT_CREATION_DURATION = Histogram(
    "relay_event_creation_duration_seconds",
    "Time spent creating and publishing an event.",
    ("event_type", "routing_key"),
)

WORKER_EVENTS_PROCESSED = Counter(
    "relay_worker_events_processed_total",
    "Total events processed successfully by workers.",
    ("worker_name", "event_type", "routing_key"),
)
WORKER_EVENTS_FAILED = Counter(
    "relay_worker_events_failed_total",
    "Total event processing failures in workers.",
    ("worker_name", "event_type", "routing_key"),
)
WORKER_EVENTS_RETRIED = Counter(
    "relay_worker_events_retried_total",
    "Total events sent to retry queues by workers.",
    ("worker_name", "routing_key", "retry_queue", "retry_count"),
)
WORKER_EVENTS_DEAD_LETTERED = Counter(
    "relay_worker_events_dead_lettered_total",
    "Total events sent to DLQ by workers.",
    ("worker_name", "routing_key"),
)
WORKER_EVENTS_BY_STATUS = Counter(
    "relay_worker_events_total",
    "Total worker event transitions by status.",
    ("worker_name", "routing_key", "status"),
)
EVENT_PROCESSING_DURATION = Histogram(
    "relay_event_processing_duration_seconds",
    "Time spent processing an event in a worker.",
    ("worker_name", "event_type", "routing_key", "status"),
)

DEAD_LETTER_EVENTS_CURRENT = Gauge(
    "relay_dead_letter_events_total",
    "Current number of events stored in the DLQ table.",
)
DEAD_LETTER_OLDEST_AGE = Gauge(
    "relay_dead_letter_oldest_event_age_seconds",
    "Approximate age in seconds of the oldest DLQ event.",
)
DEAD_LETTER_REPROCESS = Counter(
    "relay_dead_letter_reprocess_total",
    "Total manual DLQ reprocess requests.",
    ("routing_key",),
)
DEAD_LETTER_REPROCESS_FAILURES = Counter(
    "relay_dead_letter_reprocess_failures_total",
    "Total failed manual DLQ reprocess requests.",
    ("routing_key", "reason"),
)

EVENTS_BY_STATUS_CURRENT = Gauge(
    "relay_events_by_status",
    "Current number of events by status.",
    ("status",),
)
EVENT_ATTEMPTS_BY_STATUS_CURRENT = Gauge(
    "relay_event_attempts_by_status",
    "Current number of event attempts by status.",
    ("status",),
)


def record_event_created(event_type: str, routing_key: str) -> None:
    EVENTS_CREATED.labels(event_type=event_type, routing_key=routing_key).inc()


def record_event_published(event_type: str, routing_key: str) -> None:
    EVENTS_PUBLISHED.labels(event_type=event_type, routing_key=routing_key).inc()


def record_event_publish_failed(event_type: str, routing_key: str) -> None:
    EVENT_PUBLISH_FAILURES.labels(event_type=event_type, routing_key=routing_key).inc()


def observe_event_creation_duration(event_type: str, routing_key: str, duration_seconds: float) -> None:
    EVENT_CREATION_DURATION.labels(event_type=event_type, routing_key=routing_key).observe(duration_seconds)


def record_event_processed(event_type: str, routing_key: str, worker_name: str = "unknown") -> None:
    WORKER_EVENTS_PROCESSED.labels(
        worker_name=worker_name,
        event_type=event_type,
        routing_key=routing_key,
    ).inc()
    WORKER_EVENTS_BY_STATUS.labels(
        worker_name=worker_name,
        routing_key=routing_key,
        status="processed",
    ).inc()


def record_event_failed(event_type: str, routing_key: str, worker_name: str = "unknown") -> None:
    WORKER_EVENTS_FAILED.labels(
        worker_name=worker_name,
        event_type=event_type,
        routing_key=routing_key,
    ).inc()
    WORKER_EVENTS_BY_STATUS.labels(
        worker_name=worker_name,
        routing_key=routing_key,
        status="failed",
    ).inc()


def record_event_retried(
    routing_key: str,
    retry_queue: str,
    retry_count: int,
    worker_name: str = "unknown",
) -> None:
    WORKER_EVENTS_RETRIED.labels(
        worker_name=worker_name,
        routing_key=routing_key,
        retry_queue=retry_queue,
        retry_count=str(retry_count),
    ).inc()
    WORKER_EVENTS_BY_STATUS.labels(
        worker_name=worker_name,
        routing_key=routing_key,
        status="retry",
    ).inc()


def record_event_dead_lettered(routing_key: str, worker_name: str = "unknown") -> None:
    WORKER_EVENTS_DEAD_LETTERED.labels(
        worker_name=worker_name,
        routing_key=routing_key,
    ).inc()
    WORKER_EVENTS_BY_STATUS.labels(
        worker_name=worker_name,
        routing_key=routing_key,
        status="dead_letter",
    ).inc()


def observe_event_processing_duration(
    event_type: str,
    routing_key: str,
    status: str,
    duration_seconds: float,
    worker_name: str = "unknown",
) -> None:
    EVENT_PROCESSING_DURATION.labels(
        worker_name=worker_name,
        event_type=event_type,
        routing_key=routing_key,
        status=status,
    ).observe(duration_seconds)


def record_dead_letter_reprocess(routing_key: str) -> None:
    DEAD_LETTER_REPROCESS.labels(routing_key=routing_key).inc()


def record_dead_letter_reprocess_failed(routing_key: str, reason: str) -> None:
    DEAD_LETTER_REPROCESS_FAILURES.labels(routing_key=routing_key, reason=reason).inc()


def refresh_database_metrics(db: Session) -> None:
    _refresh_event_status_metrics(db)
    _refresh_attempt_status_metrics(db)
    _refresh_dead_letter_metrics(db)


def _refresh_event_status_metrics(db: Session) -> None:
    for status, count in db.execute(select(Event.status, func.count(Event.id)).group_by(Event.status)):
        EVENTS_BY_STATUS_CURRENT.labels(status=status).set(count)


def _refresh_attempt_status_metrics(db: Session) -> None:
    for status, count in db.execute(
        select(EventAttempt.status, func.count(EventAttempt.id)).group_by(EventAttempt.status)
    ):
        EVENT_ATTEMPTS_BY_STATUS_CURRENT.labels(status=status).set(count)


def _refresh_dead_letter_metrics(db: Session) -> None:
    total = db.scalar(select(func.count(DeadLetterEvent.id))) or 0
    DEAD_LETTER_EVENTS_CURRENT.set(total)

    oldest_created_at = db.scalar(select(func.min(DeadLetterEvent.created_at)))
    if oldest_created_at is None:
        DEAD_LETTER_OLDEST_AGE.set(0)
        return

    if oldest_created_at.tzinfo is None:
        oldest_created_at = oldest_created_at.replace(tzinfo=UTC)
    DEAD_LETTER_OLDEST_AGE.set(max((datetime.now(UTC) - oldest_created_at).total_seconds(), 0))
