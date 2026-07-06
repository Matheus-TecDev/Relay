from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.event import EventRead


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


class DeadLetterEventListItem(BaseModel):
    id: str
    event_id: str
    reason: str
    error_message: str | None
    retry_count: int
    original_routing_key: str | None
    created_at: datetime
    correlation_id: str
    trace_id: str
    event_status: str


class DeadLetterEventDetail(BaseModel):
    id: str
    event_id: str
    reason: str
    payload: dict[str, Any]
    retry_count: int
    original_routing_key: str | None
    error_message: str | None
    created_at: datetime
    event: EventRead
    attempts: list[EventAttemptRead]
    logs: list[EventLogRead]


class DeadLetterReprocessResponse(BaseModel):
    dead_letter_event_id: str
    event_id: str
    status: str
    routing_key: str
    correlation_id: str
    trace_id: str
