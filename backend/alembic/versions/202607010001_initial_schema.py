"""Initial schema

Revision ID: 202607010001
Revises:
Create Date: 2026-07-01 00:01:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "202607010001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("routing_key", sa.String(length=120), nullable=True),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column("trace_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_created_at", "events", ["created_at"], unique=False)
    op.create_index("ix_events_correlation_id", "events", ["correlation_id"], unique=False)
    op.create_index("ix_events_event_type", "events", ["event_type"], unique=False)
    op.create_index("ix_events_status", "events", ["status"], unique=False)
    op.create_index("ix_events_trace_id", "events", ["trace_id"], unique=False)

    op.create_table(
        "dead_letter_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("original_routing_key", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dead_letter_events_event_id", "dead_letter_events", ["event_id"], unique=False)

    op.create_table(
        "event_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_attempts_event_id", "event_attempts", ["event_id"], unique=False)

    op.create_table(
        "event_processing_states",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("worker_name", sa.String(length=120), nullable=True),
        sa.Column("routing_key", sa.String(length=120), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(
        "ix_event_processing_states_processing_started_at",
        "event_processing_states",
        ["processing_started_at"],
        unique=False,
    )
    op.create_index("ix_event_processing_states_status", "event_processing_states", ["status"], unique=False)
    op.create_index(
        "ix_event_processing_states_worker_name",
        "event_processing_states",
        ["worker_name"],
        unique=False,
    )

    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("exchange", sa.String(length=120), nullable=False),
        sa.Column("routing_key", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_outbox_messages_event_id", "outbox_messages", ["event_id"], unique=False)
    op.create_index("ix_outbox_messages_locked_at", "outbox_messages", ["locked_at"], unique=False)
    op.create_index("ix_outbox_messages_next_attempt_at", "outbox_messages", ["next_attempt_at"], unique=False)
    op.create_index("ix_outbox_messages_status", "outbox_messages", ["status"], unique=False)

    op.create_table(
        "event_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=255), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_logs_event_id", "event_logs", ["event_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_event_logs_event_id", table_name="event_logs")
    op.drop_table("event_logs")
    op.drop_index("ix_outbox_messages_status", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_next_attempt_at", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_locked_at", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_event_id", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index("ix_event_processing_states_worker_name", table_name="event_processing_states")
    op.drop_index("ix_event_processing_states_status", table_name="event_processing_states")
    op.drop_index("ix_event_processing_states_processing_started_at", table_name="event_processing_states")
    op.drop_table("event_processing_states")
    op.drop_index("ix_event_attempts_event_id", table_name="event_attempts")
    op.drop_table("event_attempts")
    op.drop_index("ix_dead_letter_events_event_id", table_name="dead_letter_events")
    op.drop_table("dead_letter_events")
    op.drop_index("ix_events_status", table_name="events")
    op.drop_index("ix_events_event_type", table_name="events")
    op.drop_index("ix_events_correlation_id", table_name="events")
    op.drop_index("ix_events_created_at", table_name="events")
    op.drop_index("ix_events_trace_id", table_name="events")
    op.drop_table("events")
