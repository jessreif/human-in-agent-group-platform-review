"""Worker container healthcheck: ``python -m app.workers.healthcheck``.

Healthy when the heartbeat file was rewritten within three heartbeat intervals.
"""

from __future__ import annotations

import json
import sys
import time

from app.workers.main import HEARTBEAT_INTERVAL_S, HEARTBEAT_PATH


def main() -> int:
    try:
        age = time.time() - HEARTBEAT_PATH.stat().st_mtime
        heartbeat = json.loads(HEARTBEAT_PATH.read_text())
        generation_age = time.time() - float(heartbeat["generation_progress_at"])
        if generation_age > 120:
            return 1
    except (OSError, ValueError, KeyError, TypeError):
        return 1
    return 0 if age <= 3 * HEARTBEAT_INTERVAL_S else 1


if __name__ == "__main__":
    sys.exit(main())
