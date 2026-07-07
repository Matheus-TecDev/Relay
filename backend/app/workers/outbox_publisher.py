import logging
import time

from prometheus_client import start_http_server

from app.core.config import settings
from app.db.session import SessionLocal
from app.observability.logging import configure_logging
from app.observability.tracing import configure_tracing
from app.services.outbox_service import acquire_next_outbox_message, publish_outbox_message

logger = logging.getLogger(__name__)


def publish_available_messages(
    publisher_name: str | None = None,
    batch_size: int | None = None,
) -> int:
    resolved_publisher_name = publisher_name or settings.outbox_publisher_name
    resolved_batch_size = batch_size or settings.outbox_batch_size
    published_or_attempted = 0

    for _ in range(resolved_batch_size):
        with SessionLocal() as db:
            outbox_message = acquire_next_outbox_message(db, resolved_publisher_name)
            if outbox_message is None:
                db.commit()
                break

            publish_outbox_message(db, outbox_message, resolved_publisher_name)
            published_or_attempted += 1

    return published_or_attempted


def run_publisher() -> None:
    configure_logging(settings.outbox_publisher_name)
    configure_tracing(settings.outbox_publisher_name)
    start_http_server(settings.outbox_metrics_port)
    logger.info(
        "Starting Relay outbox publisher",
        extra={
            "publisher_name": settings.outbox_publisher_name,
            "batch_size": settings.outbox_batch_size,
            "poll_interval_seconds": settings.outbox_poll_interval_seconds,
            "metrics_port": settings.outbox_metrics_port,
        },
    )

    while True:
        processed = publish_available_messages()
        if processed == 0:
            time.sleep(settings.outbox_poll_interval_seconds)


def main() -> None:
    run_publisher()


if __name__ == "__main__":
    main()
