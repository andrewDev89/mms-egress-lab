"""Structured events for the lab MMSC side; no message content or phone numbers."""
import json
import logging
import sys
from datetime import datetime, timezone

logger = logging.getLogger("mms.events")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def log_event(component, event, level="info", **fields):
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "component": component,
        "event": event,
        **fields,
    }
    logger.log(getattr(logging, level.upper()), json.dumps(record, default=str))
