from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import EventProcessingStatus
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.event import Event


class EventProcessingState(Base):
    __tablename__ = "event_processing_states"
    __table_args__ = (
        Index("ix_event_processing_states_status", "status"),
        Index("ix_event_processing_states_processing_started_at", "processing_started_at"),
        Index("ix_event_processing_states_worker_name", "worker_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=EventProcessingStatus.PENDING.value,
    )
    worker_name: Mapped[str] = mapped_column(String(120), nullable=True)
    routing_key: Mapped[str] = mapped_column(String(120), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    event: Mapped["Event"] = relationship(back_populates="processing_state")
