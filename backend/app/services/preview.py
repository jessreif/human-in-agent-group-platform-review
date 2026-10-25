"""Signed preview descriptors and the entry override guard (INV-0209, spec §5.1, §11).

A preview descriptor is issued by an authenticated researcher (the admin endpoint is INV-1302)
and fixes profile, arm, seed, deployment mode and timing mode. It is an HMAC-SHA256-signed,
expiring token; nothing in it can be changed without the key. Rules:

- descriptors can only ever be ``preview`` or ``qa`` — ``mode="live"`` is not a legal
  descriptor, so no link can impersonate a fielded session. Non-live deployments accept
  only their own descriptor mode; a ``live`` deployment additionally accepts any
  descriptor as an analytically-excluded test session (2026-10-07 amendment: lab testers
  mint arm-pinned test links on the fielding deployment itself — never consume
  allocations or payment);
- the descriptor pins the manifest SHA-256 it was issued for, so a republished profile
  invalidates older links instead of silently switching content;
- accelerated timing exists only as an explicit QA overlay (``accelerated_qa`` in ``qa``
  mode); faithful and accelerated previews are distinct and neither is analytic;
- a URL parameter can never select condition, profile, seed, world or speed: the entry guard
  rejects any such override key outright, in every mode.

The signing key is supplied by the caller. Wiring it to a setting and to ``POST /entry`` is
part of INV-0401/0406/1302; until then this contract is exercised by unit tests only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, ValidationError

from app.domain.manifests.canonical import canonical_bytes
from app.domain.manifests.schema import AllocationArm, Strict

TOKEN_DOMAIN = b"investigators.preview/1."
MIN_KEY_BYTES = 32
MAX_LIFETIME = timedelta(days=7)
FORBIDDEN_OVERRIDE_KEYS = frozenset(
    {
        "arm",
        "cond",
        "condition",
        "profile",
        "manifest",
        "manifest_id",
        "seed",
        "world",
        "speed",
        "accelerated",
        "timing",
        "timing_mode",
        "qa",
        "debug",
        "truth",
        "mode",
    }
)


class PreviewError(ValueError):
    pass


class PreviewDescriptor(Strict):
    v: Literal[1]
    manifest_id: str = Field(pattern=r"^[a-z0-9_]+_v[0-9]+$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    arm: AllocationArm
    seed: str = Field(pattern=r"^[0-9a-f]{32,128}$")
    mode: Literal["preview", "qa"]
    timing_mode: Literal["faithful", "accelerated_qa"]
    qa_overlay: str | None = Field(default=None, pattern=r"^[a-z0-9_.]+@v[0-9]+$")
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    nonce: str = Field(pattern=r"^[0-9a-f]{16,64}$")
    issuer: str = Field(min_length=1, max_length=120)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _check_key(key: bytes) -> None:
    if len(key) < MIN_KEY_BYTES:
        raise PreviewError("preview signing key must have at least 32 bytes")


def _signature(key: bytes, payload_b64: str) -> str:
    return _b64(hmac.new(key, TOKEN_DOMAIN + payload_b64.encode(), hashlib.sha256).digest())


def issue_descriptor(
    key: bytes,
    *,
    manifest_id: str,
    manifest_sha256: str,
    arm: AllocationArm,
    seed: str,
    mode: Literal["preview", "qa"],
    timing_mode: Literal["faithful", "accelerated_qa"],
    now: datetime,
    lifetime: timedelta,
    issuer: str,
    qa_overlay: str | None = None,
) -> str:
    _check_key(key)
    if lifetime <= timedelta(0) or lifetime > MAX_LIFETIME:
        raise PreviewError("preview lifetime must be positive and at most 7 days")
    if timing_mode == "accelerated_qa" and (mode != "qa" or qa_overlay is None):
        raise PreviewError("accelerated timing needs qa mode and an explicit qa overlay")
    if timing_mode == "faithful" and qa_overlay is not None:
        raise PreviewError("faithful previews carry no qa overlay")
    issued = int(now.timestamp())
    descriptor = PreviewDescriptor(
        v=1,
        manifest_id=manifest_id,
        manifest_sha256=manifest_sha256,
        arm=arm,
        seed=seed,
        mode=mode,
        timing_mode=timing_mode,
        qa_overlay=qa_overlay,
        issued_at=issued,
        expires_at=issued + int(lifetime.total_seconds()),
        nonce=secrets.token_hex(16),
        issuer=issuer,
    )
    payload = _b64(canonical_bytes(descriptor.model_dump(mode="json")))
    return f"{payload}.{_signature(key, payload)}"


@dataclass(frozen=True)
class PreviewGrant:
    descriptor: PreviewDescriptor
    analytic_eligible: bool = False
    consumes_allocation: bool = False
    creates_payment: bool = False


def verify_descriptor(
    key: bytes,
    token: str,
    *,
    app_mode: str,
    now: datetime,
    current_manifest_sha256: Mapping[str, str],
) -> PreviewGrant:
    """Verify signature, expiry, deployment mode and manifest pin. Fails closed."""
    _check_key(key)
    payload, sep, signature = token.partition(".")
    if not sep or not payload or not signature:
        raise PreviewError("malformed preview token")
    if not hmac.compare_digest(_signature(key, payload), signature):
        raise PreviewError("invalid preview signature")
    try:
        descriptor = PreviewDescriptor.model_validate_json(_unb64(payload))
    except (ValidationError, binascii.Error, ValueError):
        raise PreviewError("invalid preview payload") from None
    stamp = int(now.timestamp())
    if not descriptor.issued_at <= stamp < descriptor.expires_at:
        raise PreviewError("preview token expired or not yet valid")
    # A non-live deployment accepts only its own descriptor mode. A live deployment
    # accepts any descriptor: PreviewDescriptor.mode cannot be "live", so every
    # descriptor session is analytically excluded by construction.
    if app_mode != "live" and descriptor.mode != app_mode:
        raise PreviewError(f"a {descriptor.mode} preview cannot run in a {app_mode} deployment")
    pinned = current_manifest_sha256.get(descriptor.manifest_id)
    if pinned is None or pinned != descriptor.manifest_sha256:
        raise PreviewError("preview was issued for a different manifest version")
    return PreviewGrant(descriptor)


def decode_descriptor(token: str) -> PreviewDescriptor | None:
    """Parse a token's payload WITHOUT verifying its signature — bookkeeping only (the
    admin "links I issued" listing needs the nonce to join the session row). Entry
    authorization always goes through ``verify_descriptor``; never call this to grant."""
    payload, sep, _signature = token.partition(".")
    if not sep or not payload:
        return None
    try:
        return PreviewDescriptor.model_validate_json(_unb64(payload))
    except (ValidationError, binascii.Error, ValueError):
        return None


def guard_entry_overrides(query: Mapping[str, str]) -> None:
    """Reject any URL parameter that tries to choose condition, content, seed or speed.

    Platform linkage parameters (e.g. panel IDs) are handled by the entry adapters (INV-0401)
    and are never credentials; this guard only refuses experimental overrides."""
    offending = sorted(k for k in query if k.strip().lower() in FORBIDDEN_OVERRIDE_KEYS)
    if offending:
        raise PreviewError(f"experimental override parameters are not accepted: {offending}")
