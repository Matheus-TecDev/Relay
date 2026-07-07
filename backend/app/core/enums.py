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


class EventProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
