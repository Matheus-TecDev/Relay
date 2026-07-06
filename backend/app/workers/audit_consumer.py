from app.core.config import settings
from app.workers.event_worker import WorkerConfig, run_worker


def main() -> None:
    run_worker(
        WorkerConfig(
            name="relay-audit-worker",
            queue_name=settings.rabbitmq_audit_queue,
            routing_keys=("events.*", "audit.*"),
        )
    )


if __name__ == "__main__":
    main()
