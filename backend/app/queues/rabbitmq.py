import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import pika

from app.core.config import settings

QUEUE_BINDINGS = {
    settings.rabbitmq_audit_queue: ("audit.*", "events.*", "retry.*"),
    settings.rabbitmq_analytics_queue: ("analytics.*", "retry.*"),
    settings.rabbitmq_notifications_queue: ("notifications.*", "retry.*"),
}

DEAD_LETTER_ROUTING_KEY = "dead_letter.event"


@dataclass(frozen=True)
class RetryPolicy:
    retry_count: int
    queue_name: str
    routing_key: str
    ttl_ms: int


def retry_policies() -> tuple[RetryPolicy, ...]:
    return (
        RetryPolicy(1, settings.rabbitmq_retry_queue_10s, "retry.10s", 10_000),
        RetryPolicy(2, settings.rabbitmq_retry_queue_30s, "retry.30s", 30_000),
        RetryPolicy(3, settings.rabbitmq_retry_queue_5m, "retry.5m", 300_000),
    )


def retry_policy_for_count(retry_count: int) -> RetryPolicy | None:
    if retry_count > settings.rabbitmq_max_retries:
        return None

    return next((policy for policy in retry_policies() if policy.retry_count == retry_count), None)


def retry_limit_exceeded(retry_count: int) -> bool:
    if retry_count <= 0:
        return False

    return retry_count > settings.rabbitmq_max_retries or retry_policy_for_count(retry_count) is None


def _connection_parameters() -> pika.ConnectionParameters:
    credentials = pika.PlainCredentials(settings.rabbitmq_user, settings.rabbitmq_password)
    return pika.ConnectionParameters(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        virtual_host=settings.rabbitmq_vhost,
        credentials=credentials,
        heartbeat=60,
        blocked_connection_timeout=30,
    )


@contextmanager
def rabbitmq_channel() -> Iterator[pika.adapters.blocking_connection.BlockingChannel]:
    connection = pika.BlockingConnection(_connection_parameters())
    try:
        channel = connection.channel()
        yield channel
    finally:
        connection.close()


def declare_topology(channel: pika.adapters.blocking_connection.BlockingChannel) -> None:
    channel.exchange_declare(
        exchange=settings.rabbitmq_exchange,
        exchange_type="topic",
        durable=True,
    )
    channel.exchange_declare(
        exchange=settings.rabbitmq_dlx,
        exchange_type="topic",
        durable=True,
    )

    for queue_name, routing_keys in QUEUE_BINDINGS.items():
        channel.queue_declare(queue=queue_name, durable=True)
        for routing_key in routing_keys:
            channel.queue_bind(
                exchange=settings.rabbitmq_exchange,
                queue=queue_name,
                routing_key=routing_key,
            )

    channel.queue_declare(queue=settings.rabbitmq_dead_letter_queue, durable=True)
    channel.queue_bind(
        exchange=settings.rabbitmq_dlx,
        queue=settings.rabbitmq_dead_letter_queue,
        routing_key="dead_letter.#",
    )

    for policy in retry_policies():
        channel.queue_declare(
            queue=policy.queue_name,
            durable=True,
            arguments={
                "x-message-ttl": policy.ttl_ms,
                "x-dead-letter-exchange": settings.rabbitmq_exchange,
            },
        )
        channel.queue_bind(
            exchange=settings.rabbitmq_dlx,
            queue=policy.queue_name,
            routing_key=policy.routing_key,
        )


def publish_event(message: dict[str, Any], routing_key: str) -> None:
    publish_message(settings.rabbitmq_exchange, routing_key, message)


def publish_message(
    exchange: str,
    routing_key: str,
    message: dict[str, Any],
    headers: dict[str, Any] | None = None,
) -> None:
    with rabbitmq_channel() as channel:
        declare_topology(channel)
        channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=json.dumps(message).encode("utf-8"),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=pika.DeliveryMode.Persistent,
                headers=headers or {},
                correlation_id=message.get("correlation_id"),
            ),
        )


def publish_retry_event(
    message: dict[str, Any],
    original_routing_key: str,
    retry_count: int,
    error_message: str,
) -> RetryPolicy:
    policy = retry_policy_for_count(retry_count)
    if policy is None:
        raise ValueError(f"No retry policy configured for retry_count={retry_count}")

    publish_message(
        settings.rabbitmq_dlx,
        policy.routing_key,
        message,
        headers={
            "x-retry-count": retry_count,
            "x-original-routing-key": original_routing_key,
            "x-last-error": error_message,
        },
    )
    return policy


def publish_dead_letter_event(
    message: dict[str, Any],
    original_routing_key: str,
    retry_count: int,
    error_message: str,
) -> None:
    publish_message(
        settings.rabbitmq_dlx,
        DEAD_LETTER_ROUTING_KEY,
        message,
        headers={
            "x-retry-count": retry_count,
            "x-original-routing-key": original_routing_key,
            "x-error-message": error_message,
        },
    )
