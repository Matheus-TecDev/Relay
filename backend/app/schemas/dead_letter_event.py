from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import DeadLetterReprocessItemStatus

from app.schemas.event import EventAttemptRead, EventLogRead, EventRead

MAX_BATCH_REPROCESS_SIZE = 20


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


class DeadLetterBatchReprocessRequest(BaseModel):
    dead_letter_event_ids: list[UUID] = Field(min_length=1, max_length=MAX_BATCH_REPROCESS_SIZE)

    @field_validator("dead_letter_event_ids")
    @classmethod
    def reject_duplicate_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("dead_letter_event_ids must not contain duplicates")
        return value


class DeadLetterBatchReprocessItem(BaseModel):
    dead_letter_event_id: str
    status: DeadLetterReprocessItemStatus
    reason: str | None
    message: str | None
    event_id: str | None
    routing_key: str | None
    correlation_id: str | None
    trace_id: str | None


class DeadLetterBatchReprocessSummary(BaseModel):
    total: int
    queued: int
    conflict: int
    not_found: int
    failed: int


class DeadLetterBatchReprocessResponse(BaseModel):
    summary: DeadLetterBatchReprocessSummary
    results: list[DeadLetterBatchReprocessItem]
