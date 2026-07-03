import json
from contextlib import contextmanager
from typing import Any, Iterator

import pika

from app.core.config import settings

QUEUE_BINDINGS = {
    settings.rabbitmq_audit_queue: ("audit.*", "events.*"),
    settings.rabbitmq_analytics_queue: ("analytics.*",),
    settings.rabbitmq_notifications_queue: ("notifications.*",),
    settings.rabbitmq_dead_letter_queue: ("dead_letter.*",),
}


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

    for queue_name, routing_keys in QUEUE_BINDINGS.items():
        channel.queue_declare(queue=queue_name, durable=True)
        for routing_key in routing_keys:
            channel.queue_bind(
                exchange=settings.rabbitmq_exchange,
                queue=queue_name,
                routing_key=routing_key,
            )


def publish_event(message: dict[str, Any], routing_key: str) -> None:
    with rabbitmq_channel() as channel:
        declare_topology(channel)
        channel.basic_publish(
            exchange=settings.rabbitmq_exchange,
            routing_key=routing_key,
            body=json.dumps(message).encode("utf-8"),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=pika.DeliveryMode.Persistent,
            ),
        )
