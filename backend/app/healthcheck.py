"""Container healthcheck for the API: ``python -m app.healthcheck``.

Checks liveness only (``/health``); readiness is reported separately so a database outage does
not cause restart loops that would hide the outage.
"""

from __future__ import annotations

import sys
import urllib.request

URL = "http://127.0.0.1:8000/health"


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=4) as response:
            return 0 if response.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
