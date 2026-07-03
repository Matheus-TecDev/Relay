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
