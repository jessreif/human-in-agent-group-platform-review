"""Structured, redacted JSON logs with request IDs (INV-0105).

Every record is rendered to one JSON line on stdout and the *final* string (message, extras
and any traceback) passes through :func:`app.redaction.redact` before it is written.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.redaction import redact

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "investigators_request_id", default=None
)

_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None)).keys()
) | {"message", "asctime", "taskName"}


class RedactingJsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and key != "request_id" and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            # Exception messages and server details may contain bound research text.
            # Preserve category and stack locations, never exception values/locals.
            import traceback

            exc = record.exc_info[1]
            payload["exc"] = (
                type(exc).__name__
                + " "
                + " ".join(
                    f"{f.filename}:{f.lineno}:{f.name}"
                    for f in traceback.extract_tb(record.exc_info[2])
                )
            )
        return redact(json.dumps(payload, default=str, ensure_ascii=False))


def configure_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingJsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Uvicorn's own handlers would bypass redaction; route them through the root handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "alembic"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    # Access logs would include raw URLs/query strings (possible platform IDs); our middleware
    # logs method, route template, status and duration instead.
    logging.getLogger("uvicorn.access").disabled = True
