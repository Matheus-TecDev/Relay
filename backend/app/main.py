from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.dead_letter_events import router as dead_letter_events_router
from app.api.routes.events import router as events_router
from app.api.routes.health import router as health_router
from app.api.routes.metrics import router as metrics_router
from app.core.config import settings
from app.observability.logging import clear_log_context, configure_logging, set_log_context
from app.observability.tracing import configure_tracing, instrument_fastapi


def create_app() -> FastAPI:
    configure_logging(settings.otel_service_name)
    configure_tracing(settings.otel_service_name)
    app = FastAPI(title=settings.project_name)
    instrument_fastapi(app)

    @app.middleware("http")
    async def logging_context_middleware(request: Request, call_next):
        set_log_context(
            correlation_id=request.headers.get("x-correlation-id"),
            endpoint=request.url.path,
        )
        try:
            return await call_next(request)
        finally:
            clear_log_context()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(auth_router, prefix=settings.api_v1_prefix)
    app.include_router(events_router, prefix=settings.api_v1_prefix)
    app.include_router(dead_letter_events_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
