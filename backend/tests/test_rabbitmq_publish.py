from contextlib import contextmanager
from typing import Any

import pika
import pytest

from app.queues import rabbitmq


class FakeChannel:
    def __init__(self) -> None:
        self.confirm_delivery_called = False
        self.published_messages: list[dict[str, Any]] = []

    def confirm_delivery(self) -> None:
        self.confirm_delivery_called = True

    def basic_publish(self, **kwargs) -> None:
        self.published_messages.append(kwargs)


def test_publish_message_requires_broker_confirmation_and_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = FakeChannel()
    declared_channels: list[FakeChannel] = []

    @contextmanager
    def fake_rabbitmq_channel():
        yield channel

    def fake_declare_topology(declared_channel: FakeChannel) -> None:
        declared_channels.append(declared_channel)

    monkeypatch.setattr(rabbitmq, "rabbitmq_channel", fake_rabbitmq_channel)
    monkeypatch.setattr(rabbitmq, "declare_topology", fake_declare_topology)

    rabbitmq.publish_message(
        "relay.events",
        "events.created",
        {"event_id": "event-1", "correlation_id": "correlation-1"},
        headers={"x-source": "test"},
    )

    assert declared_channels == [channel]
    assert channel.confirm_delivery_called is True
    assert len(channel.published_messages) == 1
    published_message = channel.published_messages[0]
    assert published_message["exchange"] == "relay.events"
    assert published_message["routing_key"] == "events.created"
    assert published_message["mandatory"] is True
    assert published_message["properties"].delivery_mode == pika.DeliveryMode.Persistent.value
    assert published_message["properties"].headers == {"x-source": "test"}
    assert published_message["properties"].correlation_id == "correlation-1"


def test_publish_message_propagates_unroutable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnroutableChannel(FakeChannel):
        def basic_publish(self, **kwargs) -> None:
            raise pika.exceptions.UnroutableError([])

    @contextmanager
    def fake_rabbitmq_channel():
        yield UnroutableChannel()

    monkeypatch.setattr(rabbitmq, "rabbitmq_channel", fake_rabbitmq_channel)
    monkeypatch.setattr(rabbitmq, "declare_topology", lambda channel: None)

    with pytest.raises(pika.exceptions.UnroutableError):
        rabbitmq.publish_message("relay.events", "events.unbound", {"event_id": "event-1"})
