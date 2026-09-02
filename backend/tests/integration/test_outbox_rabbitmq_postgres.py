import json
import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.enums import EventStatus, OutboxStatus
from app.db.session import SessionLocal
from app.models import Event, OutboxMessage
from app.queues.rabbitmq import declare_topology, rabbitmq_channel
from app.schemas.event import EventCreate
from app.services.event_service import EventService
from app.services.outbox_service import acquire_next_outbox_message, publish_outbox_message

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_RELAY_INTEGRATION_TESTS") != "1",
        reason="Set RUN_RELAY_INTEGRATION_TESTS=1 to run integration tests.",
    ),
]


@pytest.fixture(scope="session", autouse=True)
def migrated_postgres() -> Iterator[None]:
    engine = create_engine(settings.database_url)
    assert engine.dialect.name == "postgresql"
    with engine.connect() as connection:
        version = connection.scalar(text("select version()"))
    assert version is not None
    assert "PostgreSQL" in version

    alembic_config = Config("alembic.ini")
    command.upgrade(alembic_config, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_integration_state() -> Iterator[None]:
    with SessionLocal() as db:
        db.execute(
            text(
                "truncate table event_logs, event_attempts, dead_letter_events, "
                "event_processing_states, outbox_messages, events restart identity cascade"
            )
        )
        db.commit()

    with rabbitmq_channel() as channel:
        declare_topology(channel)
        for queue_name in (
            settings.rabbitmq_audit_queue,
            settings.rabbitmq_analytics_queue,
            settings.rabbitmq_notifications_queue,
            settings.rabbitmq_dead_letter_queue,
        ):
            channel.queue_purge(queue=queue_name)

    yield


def test_outbox_publish_confirmation_with_real_postgres_and_rabbitmq() -> None:
    valid_event_id = _create_event("customer.created", "events.created")
    valid_outbox_id = _publish_next_outbox_message()

    with SessionLocal() as db:
        published_message = db.get(OutboxMessage, valid_outbox_id)
        published_event = db.get(Event, valid_event_id)

    assert published_message is not None
    assert published_message.status == OutboxStatus.PUBLISHED.value
    assert published_message.published_at is not None
    assert published_message.next_attempt_at is None
    assert published_event is not None
    assert published_event.status == EventStatus.QUEUED.value

    delivered_message = _get_message_from_queue(settings.rabbitmq_audit_queue)
    assert delivered_message is not None
    assert delivered_message["event_id"] == valid_event_id
    assert delivered_message["routing_key"] == "events.created"

    unroutable_event_id = _create_event("customer.created", "unbound.created")
    unroutable_outbox_id = _publish_next_outbox_message()

    with SessionLocal() as db:
        failed_message = db.get(OutboxMessage, unroutable_outbox_id)
        failed_event = db.get(Event, unroutable_event_id)

    assert failed_message is not None
    assert failed_message.status == OutboxStatus.FAILED.value
    assert failed_message.published_at is None
    assert failed_message.last_error is not None
    assert "unroutable" in failed_message.last_error
    assert failed_message.next_attempt_at is not None
    assert failed_message.locked_at is None
    assert failed_message.locked_by is None
    assert failed_event is not None
    assert failed_event.status == EventStatus.PUBLISH_FAILED.value


def _create_event(event_type: str, routing_key: str) -> str:
    with SessionLocal() as db:
        event = EventService(db).create_event(
            EventCreate(
                event_type=event_type,
                payload={"customer_id": routing_key},
                routing_key=routing_key,
                correlation_id=f"correlation-{routing_key}",
                trace_id=f"trace-{routing_key}",
            )
        )
        return event.id


def _publish_next_outbox_message() -> str:
    with SessionLocal() as db:
        outbox_message = acquire_next_outbox_message(db, "integration-publisher")
        assert outbox_message is not None
        outbox_message_id = outbox_message.id
        publish_outbox_message(db, outbox_message, "integration-publisher")
        return outbox_message_id


def _get_message_from_queue(queue_name: str) -> dict | None:
    with rabbitmq_channel() as channel:
        method, _properties, body = channel.basic_get(queue=queue_name, auto_ack=False)
        if method is None:
            return None
        channel.basic_ack(method.delivery_tag)
        return json.loads(body.decode("utf-8"))
