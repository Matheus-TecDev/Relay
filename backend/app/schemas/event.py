from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]
    routing_key: str | None = Field(default=None, max_length=120)
    correlation_id: str | None = Field(default=None, max_length=120)
    trace_id: str | None = Field(default=None, max_length=120)


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    payload: dict[str, Any]
    routing_key: str | None
    correlation_id: str
    trace_id: str
    status: str
    created_at: datetime
    updated_at: datetime


class EventAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    attempt_number: int
    status: str
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class EventLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    level: str
    message: str
    log_metadata: dict[str, Any]
    created_at: datetime


class EventDeadLetterEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    reason: str
    payload: dict[str, Any]
    retry_count: int
    original_routing_key: str | None
    error_message: str | None
    created_at: datetime


class EventDetail(EventRead):
    attempts: list[EventAttemptRead]
    logs: list[EventLogRead]
    dead_letter_entries: list[EventDeadLetterEntryRead]


class EventSummary(BaseModel):
    total_events: int
    by_status: dict[str, int]
    dead_letter_total: int
    oldest_dead_letter_age_seconds: float | None
    recent_events_count: int
