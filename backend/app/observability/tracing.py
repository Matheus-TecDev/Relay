from uuid import uuid4

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.pika import PikaInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, ParentBased, TraceIdRatioBased

from app.core.config import settings

_tracing_configured = False
_sqlalchemy_instrumented = False
_pika_instrumented = False


def _resource_attributes(service_name: str) -> dict[str, str]:
    attributes: dict[str, str] = {"service.name": service_name}
    for raw_attribute in settings.otel_resource_attributes.split(","):
        if "=" not in raw_attribute:
            continue
        key, value = raw_attribute.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if key and value:
            attributes[key] = value
    return attributes


def _sampler():
    sampler = settings.otel_traces_sampler.strip().lower()
    if sampler == "always_off":
        return ALWAYS_OFF
    if sampler.startswith("traceidratio:"):
        try:
            ratio = float(sampler.split(":", maxsplit=1)[1])
        except ValueError:
            ratio = 1.0
        return ParentBased(TraceIdRatioBased(max(0.0, min(1.0, ratio))))
    return ALWAYS_ON


def configure_tracing(service_name: str | None = None) -> None:
    global _pika_instrumented, _sqlalchemy_instrumented, _tracing_configured

    if not settings.tracing_enabled:
        return

    resolved_service_name = service_name or settings.otel_service_name

    if not _tracing_configured:
        provider = TracerProvider(
            resource=Resource.create(_resource_attributes(resolved_service_name)),
            sampler=_sampler(),
        )
        exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            insecure=settings.otel_exporter_otlp_insecure,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracing_configured = True

    if not _sqlalchemy_instrumented:
        from app.db.session import engine

        SQLAlchemyInstrumentor().instrument(engine=engine)
        _sqlalchemy_instrumented = True

    if not _pika_instrumented:
        PikaInstrumentor().instrument()
        _pika_instrumented = True


def instrument_fastapi(app: FastAPI) -> None:
    if not settings.tracing_enabled:
        return

    FastAPIInstrumentor.instrument_app(app)


def current_trace_id() -> str | None:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")


def generate_correlation_id() -> str:
    return str(uuid4())


def generate_trace_id() -> str:
    return current_trace_id() or str(uuid4())
