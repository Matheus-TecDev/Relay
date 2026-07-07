from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.dead_letter_event import DeadLetterEvent
from app.models.event import Event
from app.models.event_attempt import EventAttempt
from app.models.event_processing_state import EventProcessingState
from app.models.outbox_message import OutboxMessage

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
WORKER_EVENTS_DUPLICATE_SKIPPED = Counter(
    "relay_worker_events_duplicate_skipped_total",
    "Total worker messages skipped because the event was already processed or already terminal.",
    ("worker_name", "routing_key", "reason"),
)
IDEMPOTENCY_LOCK_FAILURES = Counter(
    "relay_idempotency_lock_failures_total",
    "Total idempotency lock acquisition failures.",
    ("worker_name", "routing_key", "reason"),
)
IDEMPOTENCY_STALE_PROCESSING_EVENTS = Gauge(
    "relay_idempotency_stale_processing_events_total",
    "Current number of events stuck in processing longer than the configured timeout.",
)
OUTBOX_MESSAGES_PENDING = Gauge(
    "relay_outbox_messages_pending_total",
    "Current number of outbox messages waiting for publication.",
)
OUTBOX_MESSAGES_FAILED = Gauge(
    "relay_outbox_messages_failed_total",
    "Current number of outbox messages waiting for retry after a failed publication.",
)
OUTBOX_MESSAGES_STUCK_PUBLISHING = Gauge(
    "relay_outbox_messages_stuck_publishing_total",
    "Current number of outbox messages stuck in publishing longer than the configured timeout.",
)
OUTBOX_OLDEST_PENDING_AGE = Gauge(
    "relay_outbox_oldest_pending_age_seconds",
    "Approximate age in seconds of the oldest pending or failed outbox message ready for publication.",
)
OUTBOX_MESSAGES_PUBLISHED = Counter(
    "relay_outbox_messages_published_total",
    "Total outbox messages published to RabbitMQ.",
    ("routing_key",),
)
OUTBOX_PUBLISH_FAILURES = Counter(
    "relay_outbox_publish_failures_total",
    "Total outbox publication failures.",
    ("routing_key",),
)
OUTBOX_RETRIES = Counter(
    "relay_outbox_retries_total",
    "Total outbox retries scheduled after failed publication.",
    ("routing_key", "attempt_count"),
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


def record_event_duplicate_skipped(
    routing_key: str,
    reason: str,
    worker_name: str = "unknown",
) -> None:
    WORKER_EVENTS_DUPLICATE_SKIPPED.labels(
        worker_name=worker_name,
        routing_key=routing_key,
        reason=reason,
    ).inc()


def record_idempotency_lock_failure(
    routing_key: str,
    reason: str,
    worker_name: str = "unknown",
) -> None:
    IDEMPOTENCY_LOCK_FAILURES.labels(
        worker_name=worker_name,
        routing_key=routing_key,
        reason=reason,
    ).inc()


def record_outbox_message_published(routing_key: str) -> None:
    OUTBOX_MESSAGES_PUBLISHED.labels(routing_key=routing_key).inc()


def record_outbox_publish_failed(routing_key: str) -> None:
    OUTBOX_PUBLISH_FAILURES.labels(routing_key=routing_key).inc()


def record_outbox_retry(routing_key: str, attempt_count: int) -> None:
    OUTBOX_RETRIES.labels(routing_key=routing_key, attempt_count=str(attempt_count)).inc()


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
    _refresh_idempotency_metrics(db)
    _refresh_outbox_metrics(db)


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


def _refresh_idempotency_metrics(db: Session) -> None:
    cutoff = datetime.now(UTC).timestamp() - settings.idempotency_processing_timeout_seconds
    stale_count = 0
    for processing_started_at, in db.execute(
        select(EventProcessingState.processing_started_at).where(EventProcessingState.status == "processing")
    ):
        if processing_started_at is None:
            continue
        if processing_started_at.tzinfo is None:
            processing_started_at = processing_started_at.replace(tzinfo=UTC)
        if processing_started_at.timestamp() < cutoff:
            stale_count += 1

    IDEMPOTENCY_STALE_PROCESSING_EVENTS.set(stale_count)


def _refresh_outbox_metrics(db: Session) -> None:
    pending_count = db.scalar(select(func.count(OutboxMessage.id)).where(OutboxMessage.status == "pending")) or 0
    OUTBOX_MESSAGES_PENDING.set(pending_count)

    failed_count = db.scalar(select(func.count(OutboxMessage.id)).where(OutboxMessage.status == "failed")) or 0
    OUTBOX_MESSAGES_FAILED.set(failed_count)

    now = datetime.now(UTC)
    oldest_ready_at = db.scalar(
        select(func.min(OutboxMessage.created_at)).where(OutboxMessage.status.in_(["pending", "failed"]))
    )
    if oldest_ready_at is None:
        OUTBOX_OLDEST_PENDING_AGE.set(0)
    else:
        if oldest_ready_at.tzinfo is None:
            oldest_ready_at = oldest_ready_at.replace(tzinfo=UTC)
        OUTBOX_OLDEST_PENDING_AGE.set(max((now - oldest_ready_at).total_seconds(), 0))

    cutoff = datetime.now(UTC).timestamp() - settings.outbox_publishing_timeout_seconds
    stuck_count = 0
    for locked_at, in db.execute(select(OutboxMessage.locked_at).where(OutboxMessage.status == "publishing")):
        if locked_at is None:
            continue
        if locked_at.tzinfo is None:
            locked_at = locked_at.replace(tzinfo=UTC)
        if locked_at.timestamp() < cutoff:
            stuck_count += 1

    OUTBOX_MESSAGES_STUCK_PUBLISHING.set(stuck_count)
