"""Secret-safe local JSON logs; result telemetry remains in the local SQLite store."""

import json
import logging
import os
import sys


class EventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "level": record.levelname,
                "event": record.getMessage(),
                "run_id": getattr(record, "run_id", None),
                "node": getattr(record, "node", None),
                "elapsed_ms": getattr(record, "elapsed_ms", None),
            },
            ensure_ascii=True,
        )


def configure_logging() -> None:
    logger = logging.getLogger("repotrail")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(EventFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(
        logging.INFO
        if os.environ.get("REPOTRAIL_LOG_LEVEL", "").upper() == "INFO"
        else logging.WARNING
    )
