import sys
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    project_name: str = "Relay"
    environment: str = "development"
    api_v1_prefix: str = "/api"

    database_url: str = "postgresql+psycopg://relay:relay_dev_password@localhost:5432/relay"

    auth_jwt_secret: str = "relay_dev_jwt_secret_change_me"
    auth_token_expire_minutes: int = 480
    auth_admin_username: str = "admin"
    auth_admin_password: str = "relay_admin"

    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "relay"
    rabbitmq_password: str = "relay_dev_password"
    rabbitmq_vhost: str = "/"
    rabbitmq_exchange: str = "relay.events"
    rabbitmq_dlx: str = "relay.events.dlx"
    rabbitmq_audit_queue: str = "relay.events.audit"
    rabbitmq_analytics_queue: str = "relay.events.analytics"
    rabbitmq_notifications_queue: str = "relay.events.notifications"
    rabbitmq_dead_letter_queue: str = "relay.events.dead_letter"
    rabbitmq_retry_queue_10s: str = "relay.events.retry.10s"
    rabbitmq_retry_queue_30s: str = "relay.events.retry.30s"
    rabbitmq_retry_queue_5m: str = "relay.events.retry.5m"
    rabbitmq_max_retries: int = 3

    redis_url: str = "redis://localhost:6379/0"

    idempotency_processing_timeout_seconds: int = 900
    outbox_publisher_name: str = "relay-outbox-publisher"
    outbox_batch_size: int = 10
    outbox_poll_interval_seconds: float = 2.0
    outbox_publishing_timeout_seconds: int = 300
    outbox_backoff_seconds: str = "5,30,120,300"
    outbox_metrics_port: int = 9101

    otel_enabled: bool = True
    otel_service_name: str = "relay-backend"
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_exporter_otlp_insecure: bool = True
    otel_traces_sampler: str = "always_on"
    otel_resource_attributes: str = "service.namespace=relay,deployment.environment=development"

    worker_name: str = "relay-audit-worker"
    worker_queue: str = "relay.events.audit"
    worker_routing_key: str = "events.*,audit.*"
    worker_metrics_port: int = 9102

    backend_cors_origins: str = Field(
        default="http://localhost,http://localhost:5173,http://127.0.0.1:5173"
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]

    @property
    def tracing_enabled(self) -> bool:
        return self.otel_enabled and self.environment.lower() != "test" and "pytest" not in sys.modules


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
