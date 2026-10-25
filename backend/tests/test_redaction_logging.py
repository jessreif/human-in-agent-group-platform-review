from __future__ import annotations

import io
import json
import logging

from app.logging_config import RedactingJsonFormatter, request_id_var
from app.redaction import REDACTED, redact, register_secret


def test_dsn_credentials_are_redacted() -> None:
    text = redact("could not connect to postgresql+psycopg://user:p%40ss@db:5432/x")
    assert "p%40ss" not in text
    assert f"user:{REDACTED}@db" in text


def test_secret_named_pairs_are_redacted() -> None:
    text = redact('ADMIN_PASSWORD=abc123 token: xyz {"api_key": "k-1", "safe": "ok"}')
    assert "abc123" not in text and "xyz" not in text and "k-1" not in text
    assert '"safe": "ok"' in text


def test_registered_secret_is_redacted_anywhere() -> None:
    register_secret("pl41n-s3cret")
    assert redact("prefix pl41n-s3cret suffix") == f"prefix {REDACTED} suffix"


def test_short_values_are_not_registered() -> None:
    register_secret("ab")
    assert redact("ab abc") == "ab abc"


def _capture() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingJsonFormatter("test-service"))
    logger = logging.getLogger("investigators.test.capture")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def test_formatter_emits_json_with_request_id_and_redacts_tracebacks() -> None:
    logger, stream = _capture()
    register_secret("tr4ceback-secret")
    token = request_id_var.set("req-abcdef12")
    try:
        try:
            raise RuntimeError("boom postgresql://u:tr4ceback-secret@h/db")
        except RuntimeError:
            logger.exception("failed", extra={"route": "/ready"})
    finally:
        request_id_var.reset(token)
    line = stream.getvalue().strip()
    record = json.loads(line)
    assert record["request_id"] == "req-abcdef12"
    assert record["service"] == "test-service"
    assert record["route"] == "/ready"
    assert "tr4ceback-secret" not in line
    assert "RuntimeError" in record["exc"]
