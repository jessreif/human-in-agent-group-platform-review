"""Canonical JSON and content hashing (INV-0201, spec §4.2).

Every hash in the manifest system is SHA-256 over this canonical form of the *materialized*
value (defaults filled in), never over a descriptive label or the raw file bytes. Canonical form:
UTF-8 JSON, keys sorted, no insignificant whitespace, no NaN/Infinity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(value: Any) -> str:
    return sha256_hex(canonical_bytes(value))
