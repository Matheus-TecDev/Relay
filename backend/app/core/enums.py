from enum import StrEnum


class EventStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    PUBLISH_FAILED = "publish_failed"


class EventLogLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

