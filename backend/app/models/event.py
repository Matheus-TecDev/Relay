from datetime import datetime
from typing import TYPE_CHECKING, List
from uuid import uuid4

from sqlalchemy import DateTime, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.core.enums import EventStatus

if TYPE_CHECKING:
    from app.models.dead_letter_event import DeadLetterEvent
    from app.models.event_attempt import EventAttempt
    from app.models.event_log import EventLog
    from app.models.event_processing_state import EventProcessingState
    from app.models.outbox_message import OutboxMessage


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_created_at", "created_at"),
        Index("ix_events_correlation_id", "correlation_id"),
        Index("ix_events_event_type", "event_type"),
        Index("ix_events_status", "status"),
        Index("ix_events_trace_id", "trace_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    routing_key: Mapped[str] = mapped_column(String(120), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=EventStatus.RECEIVED.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    attempts: Mapped[List["EventAttempt"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    logs: Mapped[List["EventLog"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    dead_letter_entries: Mapped[List["DeadLetterEvent"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
    )
    processing_state: Mapped["EventProcessingState"] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        uselist=False,
    )
    outbox_message: Mapped["OutboxMessage"] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        uselist=False,
    )
