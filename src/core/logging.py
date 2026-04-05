"""Structured JSON log formatter for production use."""

import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record):
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        # Django request attributes (set by Django's request logging)
        for attr in ("request_id", "user_id", "facility_id"):
            val = getattr(record, attr, None)
            if val is not None:
                entry[attr] = val
        return json.dumps(entry, ensure_ascii=False)
