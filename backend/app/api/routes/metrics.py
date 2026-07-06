from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.observability.metrics import refresh_database_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)) -> Response:
    refresh_database_metrics(db)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
