from app.core.config import settings
from app.workers.event_worker import WorkerConfig, run_worker


def main() -> None:
    run_worker(
        WorkerConfig(
            name="relay-analytics-worker",
            queue_name=settings.rabbitmq_analytics_queue,
            routing_keys=("analytics.*",),
        )
    )


if __name__ == "__main__":
    main()
