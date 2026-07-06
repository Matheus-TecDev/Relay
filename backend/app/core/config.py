from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    project_name: str = "Relay"
    environment: str = "development"
    api_v1_prefix: str = "/api"

    database_url: str = "postgresql+psycopg://relay:relay_dev_password@localhost:5432/relay"

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

    worker_name: str = "relay-audit-worker"
    worker_queue: str = "relay.events.audit"
    worker_routing_key: str = "events.*,audit.*"

    backend_cors_origins: str = Field(
        default="http://localhost,http://localhost:5173,http://127.0.0.1:5173"
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
