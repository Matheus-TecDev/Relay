from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.events import router as events_router
from app.api.routes.health import router as health_router
from app.core.config import settings
from app.observability.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title=settings.project_name)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(events_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
