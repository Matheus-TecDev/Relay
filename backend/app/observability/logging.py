import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from app.core.config import settings

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_event_id: ContextVar[str | None] = ContextVar("event_id", default=None)
_endpoint: ContextVar[str | None] = ContextVar("endpoint", default=None)

_STANDARD_LOG_RECORD_ATTRIBUTES = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


def set_log_context(
    correlation_id: str | None = None,
    event_id: str | None = None,
    endpoint: str | None = None,
) -> None:
    if correlation_id is not None:
        _correlation_id.set(correlation_id)
    if event_id is not None:
        _event_id.set(event_id)
    if endpoint is not None:
        _endpoint.set(endpoint)


def clear_log_context() -> None:
    _correlation_id.set(None)
    _event_id.set(None)
    _endpoint.set(None)


def _current_trace_fields() -> tuple[str | None, str | None]:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None, None

    return format(span_context.trace_id, "032x"), format(span_context.span_id, "016x")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        trace_id, span_id = _current_trace_fields()
        correlation_id = getattr(record, "correlation_id", None) or _correlation_id.get()
        event_id = getattr(record, "event_id", None) or _event_id.get()
        endpoint = getattr(record, "endpoint", None) or _endpoint.get()

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "service": getattr(record, "service", None) or settings.otel_service_name,
            "environment": settings.environment,
            "trace_id": getattr(record, "trace_id", None) or trace_id,
            "span_id": getattr(record, "span_id", None) or span_id,
            "correlation_id": correlation_id,
            "event_id": event_id,
            "endpoint": endpoint,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_ATTRIBUTES and key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(service_name: str | None = None) -> None:
    handler = logging.StreamHandler(sys.__stdout__ or sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    if service_name:
        settings.otel_service_name = service_name

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
