from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.event import EventAttemptRead, EventLogRead, EventRead


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
