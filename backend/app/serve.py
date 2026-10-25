"""API process entry point: ``python -m app.serve``.

Loads settings *before* starting uvicorn so that a configuration failure exits with status 2
and one value-free log line, instead of a traceback that could echo environment values.
"""

from __future__ import annotations

import logging
import sys

import uvicorn

from app.logging_config import configure_logging
from app.main import create_app
from app.settings import ConfigurationError, load_settings

PORT = 8000


def main() -> int:
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        configure_logging("investigators-api")
        logging.getLogger("investigators.api").critical(str(exc))
        return 2
    app = create_app(settings)
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 - container-internal; Compose publishes on 127.0.0.1 only
        port=PORT,
        access_log=False,
        log_config=None,
        proxy_headers=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
