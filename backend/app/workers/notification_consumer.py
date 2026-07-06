from app.core.config import settings
from app.workers.event_worker import WorkerConfig, run_worker


def main() -> None:
    run_worker(
        WorkerConfig(
            name="relay-notification-worker",
            queue_name=settings.rabbitmq_notifications_queue,
            routing_keys=("notifications.*",),
        )
    )


if __name__ == "__main__":
    main()
