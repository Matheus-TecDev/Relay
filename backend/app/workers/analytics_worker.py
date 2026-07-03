import logging
from typing import Any

logger = logging.getLogger(__name__)


def handle_event(message: dict[str, Any]) -> None:
    logger.info("Analytics handler received event_id=%s", message["event_id"])

