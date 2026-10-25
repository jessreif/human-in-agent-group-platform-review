"""Admin API (WP13; spec §3.1, §11): app-enforced auth for ``/investigators/api/admin``.

Apache strips the external prefix once, so these routes are internal ``/admin/*``. Apache
requires a Shibboleth session on this path; the application is a second, independent gate
and never consults forwarded ``X-Remote-User``/``Eppn``/``X-Shib-*`` headers
(``adminauth.UNTRUSTED_IDENTITY_HEADERS`` documents the set).
Authentication is the signed, expiring ``inv_admin`` cookie (scoped to
``/investigators/``); every mutation additionally requires the ``inv_admin_csrf``
double-submit token in ``X-CSRF-Token`` and a same-origin ``Origin`` when present.

Roles (per-role secrets — see ``app.services.adminauth`` docstring):
- ``researcher``   read views incl. restricted truth replay, previews, batch create,
                   dispositions, retention schedule, redaction review, research exports,
                   entitlement decisions, deletion.
- ``operator``     session monitor/replay (participant projection only), enrollment
                   pause/resume, technical stop, credential recovery, incidents, cleanup,
                   retention purge.
- ``compensation`` entitlement queue and mark-paid only — no research data.

Empty ``ADMIN_PASSWORD`` disables every route here (fail closed): login answers 403
``admin_disabled`` and authenticated routes answer 401.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.db import schema as s
from app.db.tx import db_now
from app.db.vocabulary import USE_ALL
from app.domain.manifests.binding import parse_timing_mode
from app.domain.manifests.store import ContentStore
from app.export.csvsafe import (
    EXPORT_FILE_MODE,
    columns_of,
    csv_bytes,
    export_dir,
    sha256_bytes,
    write_csv,
)
from app.export.restricted import payment_export, sharing_bundle
from app.export.study import (
    DOWNLOAD_FREE_TEXT_TABLES,
    EXPORT_TABLE_NAMES,
    download_table,
    export_study,
    flag_columns,
    session_rows_for_ids,
    table_columns,
    write_export,
)
from app.export.study import MANIFEST_SCHEMA as EXPORT_MANIFEST_SCHEMA
from app.flags import DEFAULT_FLAGS, FeatureFlags
from app.services import (
    adminauth,
    adminops,
    adminviews,
    dashboard_analysis,
    dashboard_exports,
    dashboard_ops,
    preview,
    retention,
)
from app.services import dashboard as dash
from app.services.enrollment import EnrollmentTarget
from app.settings import Settings

router = APIRouter()

MAX_EXPORT_SESSIONS = 5000
MAX_ID_LIST = 500


def _problem(status: int, kind: str, detail: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        {"error": kind, "detail": detail, **extra},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def _store(request: Request) -> ContentStore:
    return cast(ContentStore, request.app.state.content_store)


def _throttle(request: Request) -> adminauth.LoginThrottle:
    """Per-app limiter so tests/deployments never share a bucket."""
    throttle = getattr(request.app.state, "admin_throttle", None)
    if throttle is None:
        settings = _settings(request)
        throttle = adminauth.LoginThrottle(
            max_attempts=settings.ADMIN_LOGIN_MAX_ATTEMPTS,
            window_s=settings.ADMIN_LOGIN_WINDOW_SECONDS,
        )
        request.app.state.admin_throttle = throttle
    return throttle


def _principal(request: Request) -> adminauth.Principal | JSONResponse:
    settings = _settings(request)
    principal = adminauth.verify_session_token(
        settings, request.cookies.get(adminauth.SESSION_COOKIE)
    )
    if principal is None:
        return _problem(401, "unauthenticated", "no valid admin session")
    return principal


def _require(request: Request, *roles: str) -> adminauth.Principal | JSONResponse:
    principal = _principal(request)
    if isinstance(principal, JSONResponse):
        return principal
    if principal.role not in roles:
        return _problem(403, "forbidden_role", f"role {principal.role} cannot use this endpoint")
    return principal


def _mutation_gate(request: Request) -> JSONResponse | None:
    """Origin + CSRF for mutating admin calls (checked before auth so a forged request
    learns nothing about session state)."""
    if not adminauth.check_origin(request):
        return _problem(403, "bad_origin", "cross-origin admin mutation refused")
    if not adminauth.check_csrf(request):
        return _problem(403, "bad_csrf", "csrf token mismatch")
    return None


class AdminProblem(Exception):  # noqa: N818 - carries a ready response, not an error state
    """Raised by the admin guards; ``admin_problem_handler`` returns the response
    unchanged, so the wire contract (``{"error", "detail"}``) is identical to the
    old early-return style (audit M3)."""

    def __init__(self, response: JSONResponse) -> None:
        super().__init__(response.status_code)
        self.response = response


async def admin_problem_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AdminProblem)
    return exc.response


def _mutation_guard(request: Request) -> None:
    """Origin + CSRF only — for routes whose role depends on the parsed body."""
    gate = _mutation_gate(request)
    if gate is not None:
        raise AdminProblem(gate)


def _guard(request: Request, *roles: str, mutate: bool = False) -> adminauth.Principal:
    """One call per route: origin + CSRF first for mutations (a forged request
    learns nothing about session state), then authentication and role."""
    if mutate:
        _mutation_guard(request)
    principal = _require(request, *roles)
    if isinstance(principal, JSONResponse):
        raise AdminProblem(principal)
    return principal


async def _body[BodyT: BaseModel](request: Request, model: type[BodyT], expected: str) -> BodyT:
    """Parse a JSON body into ``model`` or refuse with ``422 malformed_body``."""
    try:
        return model.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        raise AdminProblem(_problem(422, "malformed_body", expected)) from exc


def _audit_login(request: Request, *, action: str, role: str, actor: str) -> None:
    """Login bookkeeping is best-effort: the auth decision never depends on the audit
    write (a DB outage must not lock operators out of the surface that diagnoses it).
    Audited *data* actions still write admin_audit inside their own transaction."""
    try:
        with _engine(request).begin() as conn:
            adminops.audit(conn, actor=actor, action=action, payload={"role": role})
    except DBAPIError:
        pass


def _session_id(raw: str) -> uuid.UUID | JSONResponse:
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError):
        return _problem(422, "bad_session_id", "session_id must be a UUID")


def _run_action(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run an adminops/retention call and map its refusals to problem responses."""
    try:
        return fn(*args, **kwargs)
    except (adminops.AdminActionError, retention.RetentionError) as exc:
        return _problem(exc.status, exc.reason, exc.reason)


# ----- auth ---------------------------------------------------------------------------------


class LoginBody(BaseModel):
    role: str = Field(max_length=32)
    password: str = Field(min_length=1, max_length=512)
    email: str | None = Field(default=None, max_length=254)


@router.post("/admin/login")
async def admin_login(request: Request) -> Any:
    if not adminauth.check_origin(request):
        return _problem(403, "bad_origin", "cross-origin admin login refused")
    settings = _settings(request)
    if not settings.admin_enabled:
        return _problem(403, "admin_disabled", "admin is disabled (ADMIN_PASSWORD empty)")
    try:
        body = LoginBody.model_validate(await request.json())
    except (ValidationError, ValueError):
        return _problem(422, "malformed_login", "expected {role, password, email?}")
    throttle = _throttle(request)
    key = (adminauth.client_ip(request), body.role)
    if not throttle.allowed(key):
        return _problem(429, "login_rate_limited", "too many login attempts; slow down")
    throttle.record(key)
    if body.email and body.email.strip().lower() not in settings.admin_emails:
        _audit_login(request, action="admin.login_failed", role=body.role, actor="admin:unknown")
        return _problem(401, "invalid_credentials", "login failed")
    if not adminauth.check_password(settings, role=body.role, password=body.password):
        _audit_login(request, action="admin.login_failed", role=body.role, actor="admin:unknown")
        return _problem(401, "invalid_credentials", "login failed")
    throttle.reset(key)
    role = cast(adminauth.Role, body.role)
    actor = body.email.strip().lower() if body.email else f"admin:{role}"
    token, expires = adminauth.make_session_token(settings, role=role, actor=actor)
    _audit_login(request, action="admin.login", role=role, actor=actor)
    resp = JSONResponse(
        {
            "role": role,
            "actor": actor,
            "expires_at": expires,
            "app_mode": settings.APP_MODE.value,
        }
    )
    resp.set_cookie(adminauth.SESSION_COOKIE, token, **adminauth.cookie_settings())
    resp.set_cookie(
        adminauth.CSRF_COOKIE,
        adminauth.csrf_token(),
        **adminauth.csrf_cookie_settings(),
    )
    return resp


@router.post("/admin/logout")
async def admin_logout(request: Request) -> Any:
    _mutation_guard(request)
    principal = _principal(request)
    if isinstance(principal, JSONResponse):
        return principal
    _audit_login(request, action="admin.logout", role=principal.role, actor=principal.actor)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(adminauth.SESSION_COOKIE, path=adminauth.COOKIE_PATH)
    resp.delete_cookie(adminauth.CSRF_COOKIE, path=adminauth.COOKIE_PATH)
    return resp


@router.get("/admin/whoami")
def admin_whoami(request: Request) -> Any:
    principal = _principal(request)
    if isinstance(principal, JSONResponse):
        return principal
    return {
        "role": principal.role,
        "actor": principal.actor,
        "expires_at": principal.expires_at,
        "admin_emails": list(_settings(request).admin_emails),
        "app_mode": _settings(request).APP_MODE.value,
    }


# ----- sessions: monitor list, detail, replay --------------------------------------------------


@router.get("/admin/sessions")
def admin_sessions(
    request: Request,
    batch_id: str | None = None,
    batch_group_id: str | None = None,
    mode: str | None = None,
    stage: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> Any:
    _guard(request, "researcher", "operator")
    if q is not None and len(q) > 128:
        return _problem(422, "query_too_long", "q is bounded to 128 characters")
    with _engine(request).connect() as conn:
        return adminviews.list_sessions(
            conn,
            batch_id=batch_id,
            batch_group_id=batch_group_id,
            mode=mode,
            stage=stage,
            q=q,
            limit=limit,
            offset=offset,
        )


@router.get("/admin/sessions/{session_id}")
def admin_session_detail(request: Request, session_id: str) -> Any:
    _guard(request, "researcher", "operator")
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    with _engine(request).connect() as conn:
        detail = adminviews.session_detail(conn, sid)
    if detail is None:
        return _problem(404, "session_not_found", "no such session")
    return detail


@router.get("/admin/sessions/{session_id}/replay")
def admin_replay(request: Request, session_id: str, limit: int = 2000) -> Any:
    """Participant-view replay. ``restricted`` (truth/holdings/context/timing/claims) is
    researcher-only; the participant side is byte-identical to what the browser saw."""
    principal = _guard(request, "researcher", "operator")
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    result = adminviews.replay(
        _engine(request),
        sid,
        include_restricted=principal.role == "researcher",
        event_limit=limit,
        store=_store(request),
        flags=_settings(request).flags(),
    )
    if result is None:
        return _problem(404, "session_not_found", "no such session")
    return result


@router.get("/admin/sessions/{session_id}/audit")
def admin_session_audit(request: Request, session_id: str, limit: int = 200) -> Any:
    _guard(request, "researcher", "operator")
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    with _engine(request).connect() as conn:
        return adminviews.audit_coverage(conn, sid, limit=max(1, min(limit, 500)))


# ----- monitor + reports ----------------------------------------------------------------------


@router.get("/admin/monitor")
def admin_monitor(request: Request) -> Any:
    _guard(request, "researcher", "operator")
    with _engine(request).connect() as conn:
        return adminviews.monitor(conn)


@router.get("/admin/reports/balance")
def admin_balance(request: Request, batch_id: str | None = None) -> Any:
    _guard(request, "researcher")
    with _engine(request).connect() as conn:
        return adminviews.balance_report(conn, batch_id=batch_id)


@router.get("/admin/dispositions")
def admin_dispositions(request: Request, batch_id: str | None = None) -> Any:
    _guard(request, "researcher", "operator")
    with _engine(request).connect() as conn:
        return adminviews.dispositions(conn, batch_id=batch_id)


@router.get("/admin/audit")
def admin_audit_log(request: Request, limit: int = 200) -> Any:
    _guard(request, "researcher")
    with _engine(request).connect() as conn:
        return adminviews.audit_log(conn, limit=max(1, min(limit, 500)))


# ----- batches + previews ---------------------------------------------------------------------


@router.get("/admin/batches")
def admin_batches(request: Request) -> Any:
    _guard(request, "researcher", "operator")
    store = _store(request)
    with _engine(request).connect() as conn:
        batches = [
            dict(r)
            for r in conn.execute(s.batch.select().order_by(s.batch.c.created_at)).mappings().all()
        ]
        pins = {
            r["manifest_id"]: r["manifest_sha256"]
            for r in conn.execute(s.experiment_manifest.select()).mappings().all()
        }
    out = []
    for b in batches:
        store_sha = (
            store.manifests[b["manifest_id"]].sha256
            if b["manifest_id"] in store.manifests
            else None
        )
        pinned = pins.get(b["manifest_id"])
        pin_status = (
            "unregistered"
            if pinned is None
            else "store_drift"
            if store_sha is not None and store_sha != pinned
            else "matches"
        )
        out.append({**adminviews.jsonable(b), "manifest_pin": pin_status})
    with _engine(request).connect() as conn:
        groups = [
            {
                **adminviews.jsonable(dict(g)),
                **adminops.group_balance(
                    conn,
                    g["group_id"],
                    time_pressure_enabled=_settings(request).TIME_PRESSURE_ENABLED,
                ),
            }
            for g in conn.execute(s.batch_group.select().order_by(s.batch_group.c.created_at))
            .mappings()
            .all()
        ]
    return {"batches": out, "groups": groups}


class BatchBody(BaseModel):
    batch_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    manifest_id: str = Field(max_length=128)
    mode: str = Field(max_length=16)


@router.post("/admin/batches")
async def admin_create_batch(request: Request) -> Any:
    principal = _guard(request, "researcher", mutate=True)
    body = await _body(request, BatchBody, "expected {batch_id, manifest_id, mode}")
    return _run_action(
        adminops.create_batch,
        _engine(request),
        _store(request),
        actor=principal.actor,
        batch_id=body.batch_id,
        manifest_id=body.manifest_id,
        mode=body.mode,
        flags=_settings(request).flags(),
    )


class EnrollmentBody(BaseModel):
    open: bool
    reason: str = Field(default="", max_length=adminauth.MAX_TEXT_CHARS)


@router.post("/admin/batches/{batch_id}/enrollment")
async def admin_enrollment(request: Request, batch_id: str) -> Any:
    principal = _guard(request, "researcher", "operator", mutate=True)
    body = await _body(request, EnrollmentBody, "expected {open, reason?}")
    return _run_action(
        adminops.set_enrollment,
        _engine(request),
        batch_id,
        open_=body.open,
        actor=principal.actor,
        reason=adminauth.sanitize_text(body.reason),
        flags=_settings(request).flags(),
    )


class BatchGroupBody(BaseModel):
    group_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    batch_ids: list[str] = Field(min_length=2, max_length=8)
    mode: str = Field(max_length=16)
    enrollment_target: EnrollmentTarget | None = None


@router.post("/admin/batch-groups")
async def admin_create_batch_group(request: Request) -> Any:
    principal = _guard(request, "researcher", mutate=True)
    body = await _body(request, BatchGroupBody, "expected {group_id, batch_ids, mode}")
    return _run_action(
        adminops.create_batch_group,
        _engine(request),
        _store(request),
        actor=principal.actor,
        group_id=body.group_id,
        batch_ids=body.batch_ids,
        mode=body.mode,
        enrollment_target=body.enrollment_target,
        flags=_settings(request).flags(),
    )


@router.post("/admin/batch-groups/{group_id}/enrollment")
async def admin_group_enrollment(request: Request, group_id: str) -> Any:
    principal = _guard(request, "researcher", "operator", mutate=True)
    body = await _body(request, EnrollmentBody, "expected {open, reason?}")
    return _run_action(
        adminops.set_group_enrollment,
        _engine(request),
        group_id,
        open_=body.open,
        actor=principal.actor,
        reason=adminauth.sanitize_text(body.reason),
        flags=_settings(request).flags(),
    )


class EnrollmentTargetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_completers_per_cell: int = Field(strict=True, ge=1, le=10000)
    reason: str = Field(min_length=1, max_length=adminauth.MAX_TEXT_CHARS)


@router.post("/admin/batch-groups/{group_id}/enrollment-target")
async def admin_group_enrollment_target(request: Request, group_id: str) -> Any:
    """Raise a live group's per-cell completer target (Prolific playbook §8)."""
    principal = _guard(request, "researcher", mutate=True)
    body = await _body(
        request, EnrollmentTargetBody, "expected {target_completers_per_cell, reason}"
    )
    return _run_action(
        adminops.raise_enrollment_target,
        _engine(request),
        group_id,
        target_completers_per_cell=body.target_completers_per_cell,
        actor=principal.actor,
        reason=adminauth.sanitize_text(body.reason),
    )


def _combo_summary(
    manifest: Any,
    arm: str,
    remarks: Any = None,
    *,
    flags: FeatureFlags = DEFAULT_FLAGS,
) -> list[str]:
    """Plain-English design bullets for a preview combo (BA-103 follow-up): every
    line is read off the bound manifest so the tester sees the contract before
    minting — trust, but verify, with nothing stated that the manifest does not
    itself declare. ``ai_assistant``/``time_pressure``/``ai_qa`` are the
    deployment feature flags: a disabled capability leaves the summary entirely.
    Optional check-ins/messages use neutral operational descriptions; bound
    participant copy remains available in the session replay and materials."""
    out = [
        f"{manifest.timing.active_budget_s}-second task on a generated world "
        f"({manifest.task.n_crates} crates, 6 hideouts)"
    ]
    overlap = manifest.task.overlap
    if overlap != "independent":
        out.append(
            {
                "none": "Crate assignments: no overlap — each teammate works four of the 12 crates",
                "some": "Crate assignments: some overlap — each teammate works "
                "four of the 8 crates",
                "full": "Crate assignments: full overlap — everyone works the same 4 crates",
            }[overlap]
        )
    budget = manifest.task.participant_intel_budget
    if budget is not None:
        out.append(f"Thin case file: participant holds ~{budget} intel records")
    phases = manifest.channels.phases
    if len(phases) > 1:
        for ph in phases:
            span = f"{ph.from_active_s}-{ph.until_active_s} s"
            if ph.name == "solo_review":
                out.append(
                    f"Solo review {span} — case file and answer drafting only; "
                    + (
                        "team chat and the AI assistant are locked"
                        if flags.ai_assistant
                        else "team chat is locked"
                    )
                )
            else:
                out.append(
                    f"Team phase {span} — chat, asks, sharing"
                    + (", and the AI assistant open" if flags.ai_assistant else " open")
                )
    else:
        out.append("All channels open from the start — no solo-review period")
    if manifest.interaction_mode == "structured_and_nl":
        out.append(
            "Asking: 'Ask the team about...' crate picker plus typed chat"
            + ("; the assistant has a per-crate ask control" if flags.ai_assistant else "")
        )
    else:
        out.append("Asking: typed chat only — no structured ask controls")
    checkin = manifest.timing.checkin
    if checkin is not None and flags.time_pressure:
        spec = next((a for a in manifest.condition_set if a.arm == arm), None)
        values = getattr(getattr(spec, "manipulation", None), "teammate_values", None)
        reveal = (
            f"teammate ratings {values[0]} and {values[1]}"
            if values
            else "teammate ratings revealed"
        )
        rating = (
            "; own rating skippable" if checkin.own_rating_skippable else "; own rating required"
        )
        out.append(f"Optional check-in at {checkin.at_active_s} s: {reveal}{rating}")
        body = getattr(getattr(remarks, "artifact", None), "body", None)
        if body is not None:
            for r in body.remarks:
                who = "a teammate (coin-flipped seat)" if r.speaker == "first" else "the other"
                out.append(f"Optional teammate message at {r.at_active_s} s from {who}")
    if manifest.manipulation.kind == "evaluation_exposure":
        out.append(
            "Peer-evaluation premise shown on exposure surfaces"
            if arm == "eval"
            else "Control arm — no evaluation premise"
        )
    times = ", ".join(f"{t} s" for t in manifest.timing.scripted_requests.times_active_s)
    out.append(f"Teammates request intel at {times}")
    out.append(
        "Teammate replies: local model with validated deterministic fallback"
        if manifest.teammates.social_generation == "model_with_validated_fallback"
        else "Teammate replies: deterministic script"
    )
    if flags.ai_qa:
        out.append(
            "AI Q&A (Optional) after the comprehension check — task-rule "
            "questions answered by the local model (deterministic fallback), "
            "exchanges stored"
        )
    if flags.ai_assistant:
        model = str(manifest.assistant.provider).split("/")[-1].split("@")[0]
        out.append(f"AI assistant: {model} — answers from the case file and shared intel")
    if not flags.bonus_payment:
        out.append("No accuracy bonus — consent, copy and payment omit the $0.50 bonus")
    return out


@router.get("/admin/preview-combos")
def admin_preview_combos(request: Request) -> Any:
    """Every published profile x its study's declared arms (spec §4.1), tagged
    with ``test_group``: ``pilot_contract`` (approved + current), ``comparison``
    (newest nl_only build of a fielded study — the lab NL variant), or
    ``archive``. Issuance itself is POST /admin/previews (researcher/operator)."""
    _guard(request, "researcher", "operator")
    from app.domain.manifests.schema import NO_PRESSURE_ARM, allocation_arms

    store = _store(request)
    # AID-63 follow-up: while SCRIPTED_TEAMMATES_ENABLED=0, deterministic-only
    # (scripted-teammate) manifests are never in the default test set — they
    # demote to ``archive`` and stay deliberately mintable for preview.
    scripted_enabled = _settings(request).SCRIPTED_TEAMMATES_ENABLED
    # AI_ASSISTANT_ENABLED=0 removes the assistant experiment-wide — combo
    # summaries drop its lines and the demo links point at the no-assistant
    # playable mocks (``*_noassist`` variants).
    flags = _settings(request).flags()
    ai_enabled = flags.ai_assistant
    # CRATE_OVERLAP_ENABLED / CRATE_NUMBER_ENABLED=0 (2026-10-15): manifests
    # pinning non-legacy task shapes demote to ``archive`` exactly like the
    # scripted family — deliberately mintable, never in the default test set.
    overlap_enabled = flags.crate_overlap
    number_enabled = flags.crate_number
    # TIME_PRESSURE_ENABLED=0 (2026-10-15) removes the manipulation
    # experiment-wide — combo summaries drop the check-in/remark lines, the
    # demo links point at the no-pressure playable mocks (``*_nopress``), and
    # the high/low arm axis collapses to the single ``nopress`` allocation cell
    # (``allocation_arms``) — minting/recorded data no longer pretend the
    # study randomizes a pressure arm it does not deliver.
    pressure_enabled = flags.time_pressure
    # AI_QA_ENABLED=1 (2026-10-16) inserts the optional rules-Q&A page after
    # the comprehension check — demo links point at the ``*_aiqa`` mocks.
    ai_qa_enabled = flags.ai_qa
    # BONUS_PAYMENT_ENABLED=0 (2026-10-16) removes the $0.50 accuracy bonus
    # experiment-wide — demo links point at the ``*_nobonus`` playable mocks
    # and combo summaries note the posture; the consent artifact and payment
    # policy are substituted server-side per session.
    bonus_enabled = flags.bonus_payment
    # BA-37: the "current" consent is the latest consent.reference version carrying
    # literal approved copy — corrected-lineage manifests are current; pointer-only
    # ancestors and superseded literal versions are legacy for issuance purposes.
    latest_consent = max(
        (
            stored.artifact.version
            for stored in store.artifacts.values()
            if stored.artifact.artifact_id == "consent.reference"
            and getattr(stored.artifact.body, "text", None)
        ),
        default=0,
    )
    # UX round 3: a manifest is also superseded once a newer version of the same family
    # (``<family>_v<N>``) is published — admins see only the newest issuable build.
    newest: dict[str, int] = {}
    for stored in store.manifests.values():
        family = stored.manifest.manifest_id.rsplit("_v", 1)[0]
        newest[family] = max(newest.get(family, 0), stored.manifest.manifest_version)
    rows = []
    for mid, stored in sorted(store.manifests.items()):
        resolved, _problems = store.resolve_refs(stored.manifest)
        consent = resolved.get("instruments.consent")
        consent_current = (
            consent is not None
            and consent.artifact.version == latest_consent
            and getattr(consent.artifact.body, "text", None) is not None
        )
        current = (
            consent_current and stored.manifest.manifest_version == newest[mid.rsplit("_v", 1)[0]]
        )
        rows.append((mid, stored, consent, consent_current, current))
    # UX v6 / lab test set: the recommended combos are the approved pilot
    # contract plus the teammate-mechanism comparison pair of the same study —
    # the newest nl_only build (AI teammates, typed chat) and the newest
    # deterministic scripted-teammate build outside the pilot family (today
    # tp_demo_nl_v3 vs tp_scripted_v1: identical single-phase tasks differing in
    # whether teammates draw on the local LLM). An LLM structured demo such as
    # tp_demo_v6 is NOT the contrast and stays archived. Comparison manifests
    # keep their reference status — this only groups them for display.
    pilot_families: set[str] = set()
    pilot_studies: set[str] = set()
    for mid, stored, _, _, current in rows:
        if current and stored.manifest.material_status == "approved":
            pilot_families.add(mid.rsplit("_v", 1)[0])
            pilot_studies.add(stored.manifest.study_key)
    nl_newest: dict[str, tuple[int, str]] = {}
    scripted_newest: dict[str, tuple[int, str]] = {}
    for mid, stored, _, _, _ in rows:
        m = stored.manifest
        if m.study_key not in pilot_studies or mid.rsplit("_v", 1)[0] in pilot_families:
            continue
        scripted = m.teammates.social_generation == "deterministic_only"
        if scripted and not scripted_enabled:
            continue
        if m.interaction_mode == "nl_only":
            pool = nl_newest
        elif scripted:
            pool = scripted_newest
        else:
            continue
        if m.manifest_version > pool.get(m.study_key, (0, ""))[0]:
            pool[m.study_key] = (m.manifest_version, mid)
    comparison_ids = {mid for pool in (nl_newest, scripted_newest) for _, mid in pool.values()}
    # 2026-10-08 four-condition launch: once approved, live-eligible teammate-mechanism
    # builds exist (tp_ai_v2 AI teammates / tp_scripted_v4 scripted teammates — both
    # typed chat, so pressure and teammate engine are the only cell differences),
    # they ARE the comparison set and supersede the preview-only reference pair.
    launch_ids = {
        mid
        for mid, stored, _, _, current in rows
        if current
        and stored.manifest.material_status == "approved"
        and (
            scripted_enabled or stored.manifest.teammates.social_generation != "deterministic_only"
        )
        and (
            stored.manifest.interaction_mode == "nl_only"
            or stored.manifest.teammates.social_generation == "deterministic_only"
        )
        # crate-condition flags off → gated task shapes are not live-eligible
        and (overlap_enabled or stored.manifest.task.overlap == "independent")
        and (number_enabled or stored.manifest.task.participant_intel_budget is None)
    }
    launch_studies = {store.manifests[mid].manifest.study_key for mid in launch_ids}
    comparison_ids = launch_ids | {
        mid
        for mid in comparison_ids
        if store.manifests[mid].manifest.study_key not in launch_studies
    }
    # v15 (2026-10-17, owner direction): the pilot contract is the newest-published
    # approved launch-eligible build per study — the contract the next round
    # fields. Only contract combos link a playable mock; every other build stays
    # mintable but offers no walk-the-screens link.
    contract_ids = {
        max(
            (mid for mid in launch_ids if store.manifests[mid].manifest.study_key == study),
            key=lambda mid: (
                getattr(store.published.manifests.get(mid), "published_on", ""),
                store.manifests[mid].manifest.manifest_version,
            ),
        )
        for study in launch_studies
    }
    combos = []
    for mid, stored, consent, consent_current, current in rows:
        m = stored.manifest
        scripted = m.teammates.social_generation == "deterministic_only"
        gated_overlap = m.task.overlap != "independent" and not overlap_enabled
        gated_thin = m.task.participant_intel_budget is not None and not number_enabled
        if (scripted and not scripted_enabled) or gated_overlap or gated_thin:
            test_group = "archive"
        elif mid in contract_ids:
            test_group = "pilot_contract"
        elif mid in comparison_ids:
            test_group = "comparison"
        else:
            test_group = "archive"
        for arm in allocation_arms(m.study_key, m.arms, time_pressure_enabled=pressure_enabled):
            # UX v6: each default-visible TP combo links its playable mock,
            # chosen by interaction mode and teammate engine — nosolo_nl for
            # typed-chat AI builds (tp_ai_v4), nosolo_nl_scripted for typed-chat
            # deterministic builds (tp_scripted_v6), and the structured
            # variants for archived ask-picker references. Archive combos get
            # no mock link.
            demo_variant: str | None = None
            if m.study_key == "investigators_time_pressure":
                if m.task.overlap != "independent" or m.task.participant_intel_budget is not None:
                    # Overlap / thin-file condition builds: the playable mock
                    # is the pre-flag review surface, so it is linked even
                    # while the manifest sits in archive under the gates.
                    # Only variants with generated mocks map — an unmocked
                    # combination links nothing rather than a missing file.
                    demo_variant = {
                        ("none", False): "ovnone",
                        ("some", False): "ovsome",
                        ("full", False): "ovfull",
                        ("none", True): "ovnone_thin",
                        ("some", True): "ovsome_thin",
                        ("full", True): "ovfull_thin",
                    }.get((m.task.overlap, m.task.participant_intel_budget is not None))
                elif test_group in ("pilot_contract", "comparison"):
                    if m.interaction_mode == "nl_only":
                        demo_variant = "nosolo_nl_scripted" if scripted else "nosolo_nl"
                    else:
                        demo_variant = "nosolo_non-nl" if scripted else "nosolo_structured"
                if demo_variant is not None and not ai_enabled:
                    demo_variant += "_noassist"
                if demo_variant is not None and not pressure_enabled:
                    demo_variant += "_nopress"
                if demo_variant is not None and ai_qa_enabled:
                    demo_variant += "_aiqa"
                if demo_variant is not None and not bonus_enabled:
                    demo_variant += "_nobonus"
                if test_group != "pilot_contract":
                    # v15: playable mocks link only on the contract build's
                    # combos — every other manifest stays mintable, no mock.
                    demo_variant = None
            combos.append(
                {
                    "manifest_id": mid,
                    "arm": arm,
                    "study_key": m.study_key,
                    "material_status": m.material_status,
                    "profile_kind": m.profile_kind,
                    "interaction_mode": m.interaction_mode,
                    "social_generation": m.teammates.social_generation,
                    "crate_overlap": m.task.overlap,
                    "intel_budget": m.task.participant_intel_budget,
                    "test_group": test_group,
                    "demo_url": (
                        f"demos/investigators_{demo_variant}_demo.html"
                        if demo_variant and arm == NO_PRESSURE_ARM
                        else f"demos/investigators_{demo_variant}_{arm}_demo.html"
                        if demo_variant
                        else None
                    ),
                    "manifest_sha256": stored.sha256,
                    "consent_key": consent.key if consent is not None else None,
                    "consent_current": consent_current,
                    "current": current,
                    "summary": _combo_summary(
                        m,
                        arm,
                        store.resolve_refs(m)[0].get("manipulation.remarks"),
                        flags=flags,
                    ),
                }
            )
    return {
        "combos": combos,
        "timing_modes": ["faithful", "accelerated_qa"],
        "scripted_teammates_enabled": scripted_enabled,
        "ai_assistant_enabled": ai_enabled,
        "crate_overlap_enabled": overlap_enabled,
        "crate_number_enabled": number_enabled,
        "time_pressure_enabled": pressure_enabled,
        "ai_qa_enabled": ai_qa_enabled,
        "bonus_payment_enabled": bonus_enabled,
    }


class PreviewBody(BaseModel):
    manifest_id: str = Field(max_length=128)
    arm: str = Field(max_length=16)
    timing_mode: str = Field(default="faithful", max_length=32)
    qa_overlay: str | None = Field(default=None, max_length=200)
    seed: str | None = Field(default=None, max_length=128)
    lifetime_hours: float = Field(default=24, gt=0, le=168)


@router.post("/admin/previews")
async def admin_issue_preview(request: Request) -> Any:
    """Issue a signed preview descriptor (INV-1302). Preview sessions never consume
    allocations or create payment rows — that is enforced at ``services.preview`` +
    ``services.entry`` (the descriptor path bypasses allocation entirely)."""
    _mutation_guard(request)
    # Operator included for lab self-service: issuing a preview link is lower-risk than
    # the technical-stop/credential-recovery actions the role already carries, and lab
    # testers log in with whichever password they were given.
    principal = _guard(request, "researcher", "operator")
    settings = _settings(request)
    key_raw = settings.PREVIEW_SIGNING_KEY.get_secret_value().strip()
    if not key_raw:
        return _problem(403, "previews_unavailable", "PREVIEW_SIGNING_KEY is not configured")
    body = await _body(request, PreviewBody, "expected {manifest_id, arm, ...}")
    store = _store(request)
    stored = store.manifests.get(body.manifest_id)
    if stored is None:
        return _problem(404, "unknown_manifest", "no such manifest in the content store")
    from app.domain.manifests.schema import allocation_arms

    # Arms are minted under the deployed allocation set: a flag-off deployment
    # previews the single ``nopress`` cell, not the high/low split it no longer
    # records on sessions.
    allowed_arms = allocation_arms(
        stored.manifest.study_key,
        stored.manifest.arms,
        time_pressure_enabled=settings.TIME_PRESSURE_ENABLED,
    )
    if body.arm not in allowed_arms:
        return _problem(
            422,
            "invalid_arm",
            f"arm must be one of {sorted(allowed_arms)} for {body.manifest_id}",
        )
    # Descriptors can only be preview/qa (the model forbids "live"): on a live
    # deployment test links mint preview-mode sessions — analytically excluded, no
    # allocation, no payable entitlement — which is the intended lab-testing path
    # (2026-10-07 amendment).
    mode: Literal["preview", "qa"] = "qa" if settings.APP_MODE.value == "qa" else "preview"
    # ``body.arm`` already matched a member of the allowed tuple — reuse that
    # literal so the descriptor carries the typed allocation arm.
    minted_arm = next(a for a in allowed_arms if a == body.arm)
    try:
        token = preview.issue_descriptor(
            key_raw.encode("utf-8"),
            manifest_id=body.manifest_id,
            manifest_sha256=stored.sha256,
            arm=minted_arm,
            seed=body.seed or secrets.token_hex(32),
            mode=mode,
            timing_mode=parse_timing_mode(body.timing_mode),
            qa_overlay=body.qa_overlay,
            now=datetime.now().astimezone(),
            lifetime=timedelta(hours=body.lifetime_hours),
            issuer=principal.actor,
        )
    except preview.PreviewError as exc:
        return _problem(422, "preview_invalid", str(exc))
    issued = preview.decode_descriptor(token)
    with _engine(request).begin() as conn:
        adminops.audit(
            conn,
            actor=principal.actor,
            action="preview_issue",
            payload={
                "manifest_id": body.manifest_id,
                "arm": body.arm,
                "timing_mode": body.timing_mode,
                "mode": mode,
                # The nonce joins this link to its session (participant research_pid
                # is ``preview-<nonce>``); the token lets the issuer re-copy the URL
                # from "my links". Both are issuer-scoped bookkeeping, not new grants.
                "nonce": issued.nonce if issued else None,
                "token": token,
                "entry_url": f"/investigators/?pt={token}",
                "expires_at": issued.expires_at if issued else None,
            },
        )
    return {"descriptor": token, "entry_url": f"/investigators/?pt={token}"}


@router.get("/admin/previews/mine")
def admin_my_previews(request: Request) -> Any:
    """The caller's issued preview links with live session status (lab self-service)."""
    principal = _guard(request, "researcher", "operator")
    with _engine(request).connect() as conn:
        return adminviews.my_previews(conn, actor=principal.actor)


# ----- support actions (operator) --------------------------------------------------------------


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1, max_length=adminauth.MAX_TEXT_CHARS)
    notes: str = Field(default="", max_length=adminauth.MAX_TEXT_CHARS)


@router.post("/admin/sessions/{session_id}/technical_stop")
async def admin_technical_stop(request: Request, session_id: str) -> Any:
    principal = _guard(request, "operator", mutate=True)
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    body = await _body(request, ReasonBody, "expected {reason, notes?}")
    return _run_action(
        adminops.technical_stop,
        _engine(request),
        _store(request),
        sid,
        actor=principal.actor,
        reason=adminauth.sanitize_text(body.reason, max_chars=120),
        notes=adminauth.sanitize_text(body.notes),
        bonus_payment_enabled=_settings(request).BONUS_PAYMENT_ENABLED,
    )


@router.post("/admin/sessions/{session_id}/credential_recovery")
async def admin_credential_recovery(request: Request, session_id: str) -> Any:
    """Support-mediated credential re-issue: the recovery token is returned once in this
    response and never stored raw. Operator only."""
    principal = _guard(request, "operator", mutate=True)
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    body = await _body(request, ReasonBody, "expected {reason}")
    return _run_action(
        adminops.credential_recovery,
        _engine(request),
        sid,
        actor=principal.actor,
        reason=adminauth.sanitize_text(body.reason, max_chars=240),
    )


class IncidentBody(BaseModel):
    kind: str = Field(max_length=32)
    clock_effect: str = Field(max_length=16)
    notes: str = Field(default="", max_length=adminauth.MAX_TEXT_CHARS)


@router.post("/admin/sessions/{session_id}/incidents")
async def admin_open_incident(request: Request, session_id: str) -> Any:
    principal = _guard(request, "operator", mutate=True)
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    body = await _body(request, IncidentBody, "expected {kind, clock_effect, notes?}")
    return _run_action(
        adminops.open_incident,
        _engine(request),
        sid,
        actor=principal.actor,
        kind=body.kind,
        clock_effect=body.clock_effect,
        notes=adminauth.sanitize_text(body.notes),
    )


@router.post("/admin/sessions/{session_id}/incidents/{incident_id}/close")
async def admin_close_incident(request: Request, session_id: str, incident_id: str) -> Any:
    principal = _guard(request, "operator", mutate=True)
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    try:
        iid = uuid.UUID(incident_id)
    except ValueError:
        return _problem(422, "bad_incident_id", "incident_id must be a UUID")
    try:
        body = ReasonBody.model_validate(await request.json())
    except (ValidationError, ValueError):
        body = None
    return _run_action(
        adminops.close_incident,
        _engine(request),
        sid,
        iid,
        actor=principal.actor,
        notes=adminauth.sanitize_text(body.reason if body else ""),
    )


# ----- entitlements -----------------------------------------------------------------------------


class EntitlementDecisionBody(BaseModel):
    decision: str = Field(max_length=16)
    note: str = Field(default="", max_length=adminauth.MAX_TEXT_CHARS)


@router.post("/admin/entitlements/{session_id}/{kind}/decision")
async def admin_entitlement_decision(request: Request, session_id: str, kind: str) -> Any:
    principal = _guard(request, "researcher", mutate=True)
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    body = await _body(request, EntitlementDecisionBody, "expected {decision, note?}")
    return _run_action(
        adminops.decide_entitlement,
        _engine(request),
        sid,
        kind,
        decision=body.decision,
        actor=principal.actor,
        note=adminauth.sanitize_text(body.note),
    )


class PaidBody(BaseModel):
    paid_ref: str = Field(min_length=1, max_length=200)


@router.post("/admin/compensation/entitlements/{session_id}/{kind}/paid")
async def admin_mark_paid(request: Request, session_id: str, kind: str) -> Any:
    principal = _guard(request, "compensation", mutate=True)
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    body = await _body(request, PaidBody, "expected {paid_ref}")
    return _run_action(
        adminops.mark_paid,
        _engine(request),
        sid,
        kind,
        paid_ref=adminauth.sanitize_text(body.paid_ref, max_chars=200),
        actor=principal.actor,
    )


@router.get("/admin/compensation/queue")
def admin_compensation_queue(request: Request) -> Any:
    _guard(request, "compensation")
    with _engine(request).connect() as conn:
        return adminviews.compensation_queue(conn)


# ----- retention + deletion (INV-1306/1307) ------------------------------------------------------


@router.get("/admin/retention")
def admin_retention_status(request: Request) -> Any:
    _guard(request, "researcher", "operator")
    with _engine(request).begin() as conn:
        return adminviews.retention_status(conn)


class RetentionScheduleBody(BaseModel):
    study_close_at: datetime


@router.post("/admin/retention/schedule")
async def admin_retention_schedule(request: Request) -> Any:
    principal = _guard(request, "researcher", "operator", mutate=True)
    body = await _body(request, RetentionScheduleBody, "expected {study_close_at}")
    with _engine(request).begin() as conn:
        return retention.schedule_platform_purge(
            conn, study_close_at=body.study_close_at, actor=principal.actor
        )


class PurgeBody(BaseModel):
    dry_run: bool = True


@router.post("/admin/retention/purge")
async def admin_retention_purge(request: Request) -> Any:
    principal = _guard(request, "operator", mutate=True)
    body = await _body(request, PurgeBody, "expected {dry_run}")
    with _engine(request).begin() as conn:
        return retention.purge_due_links(conn, actor=principal.actor, dry_run=body.dry_run)


class DeletionBody(BaseModel):
    dry_run: bool = True
    reason: str = Field(min_length=1, max_length=adminauth.MAX_TEXT_CHARS)


@router.post("/admin/sessions/{session_id}/deletion")
async def admin_session_deletion(request: Request, session_id: str) -> Any:
    """Withdrawal/data-use disposition execution: tombstone + deletion of participant
    data. ``dry_run`` (default) returns the plan without deleting. Compensation records
    are never touched (INV-1307)."""
    principal = _guard(request, "researcher", "operator", mutate=True)
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    body = await _body(request, DeletionBody, "expected {dry_run?, reason}")
    with _engine(request).begin() as conn:
        return _run_action(
            retention.apply_deletion,
            conn,
            sid,
            actor=principal.actor,
            reason=adminauth.sanitize_text(body.reason),
            scope="session",
            dry_run=body.dry_run,
        )


class CleanupBody(BaseModel):
    scope: str = Field(default="preview", max_length=16)
    batch_id: str | None = Field(default=None, max_length=64)
    dry_run: bool = True
    reason: str = Field(default="", max_length=adminauth.MAX_TEXT_CHARS)
    confirm_live: bool = False


@router.post("/admin/cleanup")
async def admin_cleanup(request: Request) -> Any:
    """Scope-bounded cleanup (INV-1308). Defaults: ``preview`` scope and ``dry_run``.
    A live run additionally needs ``dry_run=false``, ``confirm_live=true`` and a reason —
    it is audited either way."""
    principal = _guard(request, "operator", mutate=True)
    body = await _body(request, CleanupBody, "expected {scope, batch_id?, dry_run?, reason?}")
    if body.scope == "live" and (body.dry_run or not body.confirm_live or not body.reason.strip()):
        return _problem(
            422,
            "live_scope_requires_confirmation",
            "live cleanup needs dry_run=false, confirm_live=true and a reason",
        )
    with _engine(request).begin() as conn:
        return _run_action(
            retention.apply_cleanup,
            conn,
            scope=body.scope,
            actor=principal.actor,
            reason=adminauth.sanitize_text(body.reason) or f"cleanup:{body.scope}",
            batch_id=body.batch_id,
            dry_run=body.dry_run,
        )


class ReapplyBody(BaseModel):
    confirm: bool = False


@router.post("/admin/retention/reapply")
async def admin_reapply_tombstones(request: Request) -> Any:
    """Re-apply deletion tombstones (post-restore path — INV-1307)."""
    principal = _guard(request, "operator", mutate=True)
    try:
        body = ReapplyBody.model_validate(await request.json())
    except (ValidationError, ValueError):
        body = ReapplyBody()
    if not body.confirm:
        return _problem(422, "confirm_required", "pass {confirm: true} to re-apply tombstones")
    with _engine(request).begin() as conn:
        return {"reapplied": retention.reapply_tombstones(conn, actor=principal.actor)}


# ----- redaction review + export queues (INV-1308) ----------------------------------------------


@router.get("/admin/redaction/queue")
def admin_redaction_queue(request: Request, limit: int = 200) -> Any:
    _guard(request, "researcher")
    with _engine(request).connect() as conn:
        return adminviews.redaction_queue(conn, limit=max(1, min(limit, 500)))


class RedactionBody(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    source_ref: str = Field(min_length=1, max_length=512)
    session_id: str | None = None
    scrubbed_ref: str | None = Field(default=None, max_length=512)
    scrubbed_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    notes: str = Field(default="", max_length=adminauth.MAX_TEXT_CHARS)


@router.post("/admin/redaction/review")
async def admin_redaction_review(request: Request) -> Any:
    """Record a reviewed redaction decision (writes ``redaction_review`` + audit)."""
    principal = _guard(request, "researcher", mutate=True)
    body = await _body(request, RedactionBody, "expected {kind, source_ref, ...}")
    sid: uuid.UUID | None = None
    if body.session_id:
        parsed = _session_id(body.session_id)
        if isinstance(parsed, JSONResponse):
            return parsed
        sid = parsed
    from sqlalchemy import insert

    with _engine(request).begin() as conn:
        review_id = uuid.uuid4()
        conn.execute(
            insert(s.redaction_review).values(
                review_id=review_id,
                session_id=sid,
                kind=adminauth.sanitize_text(body.kind, max_chars=64),
                source_ref=adminauth.sanitize_text(body.source_ref, max_chars=512),
                scrubbed_ref=body.scrubbed_ref,
                scrubbed_sha256=body.scrubbed_sha256,
                reviewer=principal.actor,
                notes=adminauth.sanitize_text(body.notes) or None,
            )
        )
        adminops.audit(
            conn,
            actor=principal.actor,
            action="redaction_review",
            session_id=sid,
            payload={"review_id": str(review_id), "source_ref": body.source_ref},
        )
    return {"review_id": str(review_id)}


class ExportScopeBody(BaseModel):
    """Dashboard cohort scope for a scoped export (DA-10): the same validated
    filter set as the dashboard GETs. Server-side resolution turns this into an
    explicit session list — an empty scope means *zero* sessions, never
    unfiltered. Unknown keys are forbidden — a misspelled bound must fail
    loudly, never silently widen the cohort (DA-25)."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str | None = Field(default=None, max_length=64)
    batch_group_id: str | None = Field(default=None, max_length=64)
    study_key: str | None = Field(default=None, max_length=64)
    compatibility_key: str | None = Field(default=None, max_length=80)
    manifest_id: str | None = Field(default=None, max_length=64)
    mode: str | None = Field(default=None, max_length=8)
    since: datetime | None = None
    until: datetime | None = None


class ExportBody(BaseModel):
    kind: str = Field(max_length=16)  # study | sharing | payment
    batch_id: str | None = Field(default=None, max_length=64)
    study_key: str | None = Field(default=None, max_length=64)
    session_ids: list[str] | None = Field(default=None, max_length=MAX_ID_LIST)
    scope: ExportScopeBody | None = None


def _scope_filter(scope: ExportScopeBody) -> dash.DashboardFilter | JSONResponse:
    """Validate a scope body into the shared ``DashboardFilter`` — the identical
    predicate set the dashboard GETs accept."""
    try:
        return dash.parse_filters(
            {
                k: v
                for k, v in {
                    "batch_id": scope.batch_id,
                    "batch_group_id": scope.batch_group_id,
                    "study_key": scope.study_key,
                    "compatibility_key": scope.compatibility_key,
                    "manifest_id": scope.manifest_id,
                    "mode": scope.mode,
                    "since": scope.since.isoformat() if scope.since else None,
                    "until": scope.until.isoformat() if scope.until else None,
                }.items()
                if v is not None
            }
        )
    except dash.FilterError as exc:
        return _problem(422, "bad_scope", str(exc))


def _resolve_export_scope(
    engine: Engine, scope: ExportScopeBody
) -> tuple[list[uuid.UUID], dict[str, Any]] | JSONResponse:
    """Resolve a dashboard scope to explicit session ids under the shared
    cohort contract (DA-10): tombstones are excluded by ``select_cohort``;
    ``withdraw_all`` sessions are additionally excluded so a scoped export can
    never carry data the participant asked to have withdrawn."""
    flt = _scope_filter(scope)
    if isinstance(flt, JSONResponse):
        return flt
    with _dash_conn(engine) as conn:
        sessions = dash.select_cohort(conn, flt)
        sids = dash.session_ids(sessions)
        du = dash.latest_data_use(conn, sids)
        allowed = [sid for sid in sids if du.get(sid) != "withdraw_all"]
    return allowed, flt.normalized()


@router.post("/admin/exports")
async def admin_export(request: Request) -> Any:
    """Reviewed export queue (INV-1308): ``study``/``sharing`` are researcher products;
    ``payment`` is compensation-only (restricted linkage). Writes under ADMIN_EXPORT_DIR
    when configured; otherwise returns the manifest/row summary only."""
    _mutation_guard(request)
    body = await _body(request, ExportBody, "expected {kind, batch_id?, ...}")
    role_for = {"study": "researcher", "sharing": "researcher", "payment": "compensation"}
    wanted = role_for.get(body.kind)
    if wanted is None:
        return _problem(422, "bad_export_kind", "kind must be study, sharing or payment")
    principal = _guard(request, wanted)
    sids: list[uuid.UUID] | None = None
    if body.session_ids is not None:
        # Explicit-None semantics (DA-10): an empty list is a *zero-session*
        # selection, not "unrestricted" — it must never fall back to the full
        # cohort.
        try:
            sids = [uuid.UUID(x) for x in body.session_ids[:MAX_ID_LIST]]
        except ValueError:
            return _problem(422, "bad_session_id", "session_ids must be UUIDs")
    resolved_scope: dict[str, Any] | None = None
    if body.scope is not None:
        # DA-10: a dashboard-confirmed scope is resolved server-side into an
        # explicit session list. It is mutually exclusive with the legacy
        # explicit filters — mixing them would be ambiguous scope.
        if body.batch_id or body.study_key or body.session_ids:
            return _problem(
                422, "ambiguous_scope", "scope cannot be combined with batch/study/session_ids"
            )
        resolved = _resolve_export_scope(_engine(request), body.scope)
        if isinstance(resolved, JSONResponse):
            return resolved
        sids, resolved_scope = resolved
        if not sids:
            return _problem(
                422,
                "empty_scope",
                "the confirmed scope resolves to zero sessions — refusing to "
                "publish an unfiltered or empty export",
            )
        if len(sids) > MAX_EXPORT_SESSIONS:
            return _problem(
                422,
                "scope_too_large",
                f"scope resolves to {len(sids)} sessions; bound is {MAX_EXPORT_SESSIONS}",
            )
    engine = _engine(request)
    out_dir = _settings(request).ADMIN_EXPORT_DIR.strip()
    try:
        if body.kind == "study":
            export = export_study(
                engine,
                batch_id=body.batch_id,
                study_key=body.study_key,
                session_ids=sids,
                feature_flags=_settings(request).feature_flags(),
            )
            manifest = (
                write_export(export, Path(out_dir) / f"study-{uuid.uuid4().hex[:12]}")
                if out_dir
                else None
            )
            summary = {
                "kind": "study",
                "snapshot_time": export.snapshot_time.isoformat(),
                "tables": {n: len(t.rows) for n, t in sorted(export.tables.items())},
                "manifest": manifest,
            }
        elif body.kind == "sharing":
            if not sids:
                return _problem(
                    422,
                    "session_scope_required",
                    "sharing exports need session_ids or a scope",
                )
            with engine.connect() as conn:
                conn = conn.execution_options(isolation_level="REPEATABLE READ")
                # 2026-10-16: sharing-bundle cells emit verbatim — reviews no longer gate.
                bundle = sharing_bundle(conn, session_ids=sids)
            manifest = None
            if out_dir:
                dest = export_dir(Path(out_dir) / f"sharing-{uuid.uuid4().hex[:12]}")
                manifest = {
                    # AG-02: persist the manifest sidecar with the publication
                    # schema marker so the catalog lists real writer output.
                    "schema": EXPORT_MANIFEST_SCHEMA,
                    "kind": "sharing",
                    "feature_flags": _settings(request).feature_flags(),
                    "tables": {
                        name: {
                            "row_count": len(t.rows),
                            "file_sha256": write_csv(
                                dest / f"{name}.csv", columns_of(t.rows), t.rows
                            ),
                        }
                        for name, t in sorted(bundle.tables.items())
                    },
                }
                sharing_manifest = dest / "manifest.json"
                sharing_manifest.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                sharing_manifest.chmod(EXPORT_FILE_MODE)
            summary = {
                "kind": "sharing",
                "tables": {
                    name: {"rows": len(t.rows)} for name, t in sorted(bundle.tables.items())
                },
                "manifest": manifest,
            }
        else:  # payment — compensation role; full linkage needs the compensation DB role
            try:
                tables = payment_export(engine, session_ids=sids)
                blocked = False
            except DBAPIError:
                # Runtime lacks the platform_link grant — the restricted export is the
                # privileged path (scripts/export_study.py --kind payment, compensation DSN).
                tables, blocked = {}, True
            manifest = None
            if out_dir and not blocked:
                dest = export_dir(Path(out_dir) / f"payment-{uuid.uuid4().hex[:12]}")
                manifest = {
                    # Compensation product: the manifest carries the publication
                    # schema marker but is never listed for researchers (DA-23).
                    "schema": EXPORT_MANIFEST_SCHEMA,
                    "kind": "payment",
                    "feature_flags": _settings(request).feature_flags(),
                    "tables": {
                        name: {
                            "row_count": len(rows),
                            "file_sha256": write_csv(dest / f"{name}.csv", columns_of(rows), rows),
                        }
                        for name, rows in sorted(tables.items())
                    },
                }
                payment_manifest = dest / "manifest.json"
                payment_manifest.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                payment_manifest.chmod(EXPORT_FILE_MODE)
            summary = {
                "kind": "payment",
                "status": "blocked_privileged" if blocked else "ok",
                "tables": {n: len(r) for n, r in sorted(tables.items())},
                "manifest": manifest,
            }
    except DBAPIError:
        return _problem(503, "persistence_unavailable", "export read failed")
    # DA-10: echo the *actual* resolved scope back so the confirming researcher
    # can verify the published cohort matched what was confirmed.
    summary_out: dict[str, Any] = dict(summary)
    if resolved_scope is not None:
        summary_out["resolved_scope"] = resolved_scope
    summary_out["session_count"] = len(sids) if sids is not None else None
    with engine.begin() as conn:
        adminops.audit(
            conn,
            actor=principal.actor,
            action="export",
            payload={
                "kind": body.kind,
                "batch_id": body.batch_id,
                "study_key": body.study_key,
                "scope": resolved_scope,
                "n_sessions": len(sids) if sids else None,
                "written": bool(out_dir) and summary_out.get("manifest") is not None,
            },
        )
    return summary_out


# ----- documentation (researcher only) --------------------------------------------------------
# A fixed allowlist — doc ids map to files under Settings.ADMIN_DOCS_DIR; user input
# can only pick a key, never a path. Design/behaviors/harness docs carry restricted
# design truth, so this whole surface is researcher-gated (like restricted replay).

_DOC_FILES: dict[str, dict[str, str]] = {
    "readme": {
        "file": "README.md",
        "label": "README — repository overview and ops commands",
        "media": "text/plain",
    },
    "decisions": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_DECISIONS.md",
        "label": "Decisions register — AID/D/SQ/O rulings",
        "media": "text/plain",
    },
    "behaviors": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_BEHAVIORS.md",
        "label": "Behaviors register — AB findings",
        "media": "text/plain",
    },
    "harness": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_AI_AGENT_HARNESS.md",
        "label": "AI agent harness — personas, CLI, evidence, run log",
        "media": "text/plain",
    },
    "study_decisions": {
        "file": "docs/DECISIONS.md",
        "label": "Study decision register (INV-0004) — fielded-build rulings",
        "media": "text/plain",
    },
    "prolific_connect": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_PROLIFIC_CONNECT.md",
        "label": "Prolific/Connect fielding guide — links, entry, completion, exports",
        "media": "text/plain",
    },
    "pre_launch_checklist": {
        "file": "PRE-LAUNCH_CHECKLIST.md",
        "label": "Pre-launch checklist — pre-open verification for paid participants",
        "media": "text/plain",
    },
    "post_launch_checklist": {
        "file": "POST-LAUNCH_CHECKLIST.md",
        "label": "Post-launch checklist — data-quality evaluation after a closed round",
        "media": "text/plain",
    },
    "prolific_playbook": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_PROLIFIC_PLAYBOOK.md",
        "label": "Prolific fielding playbook — URL contract, returns, payment",
        "media": "text/plain",
    },
    "four_condition_pilot": {
        "file": "docs/FOUR_CONDITION_PROLIFIC_PILOT.md",
        "label": "Four-condition Prolific pilot — v12 selection and settings (v12-era)",
        "media": "text/plain",
    },
    "workload_plan": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_WORKLOAD_PLAN.md",
        "label": "Workload plan (v15) — felt-workload manipulation design and artifacts",
        "media": "text/plain",
    },
    "crate_overlap_plan": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_CRATE_OVERLAP_NUMBER_PLAN.md",
        "label": "Crate overlap/number plan — condition-factor plan incl. v15 mixed arm",
        "media": "text/plain",
    },
    "telemetry_plan": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_CASE_FILE_TELEMETRY_PLAN.md",
        "label": "Case-file telemetry plan — bounded kinds incl. submit_bar_view",
        "media": "text/plain",
    },
    "dashboard_plan": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_DASHBOARD_PLAN.md",
        "label": "Dashboard plan — tabs, cohort/pool contracts, v15 additions",
        "media": "text/plain",
    },
    "dashboard_refactor_audit": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_DASHBOARD_REFACTOR_AUDIT.md",
        "label": "Dashboard refactor audit (2026-10-18) — identity/label seams + remediation plan",
        "media": "text/plain",
    },
    "ui_design_playbook": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_UI_DESIGN_PLAYBOOK.md",
        "label": "UI design playbook — workbench surfaces incl. v15 record/checkin changes",
        "media": "text/plain",
    },
    "test_suite_best_practices": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_TEST_SUITE_BEST_PRACTICES.md",
        "label": "Test suite best practices — conventions + v15 project lessons",
        "media": "text/plain",
    },
    "testing_readme": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_TESTING_README.md",
        "label": "Testing README — suite layout, drivers, v15 additions",
        "media": "text/plain",
    },
    "backup_playbook": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_BACKUP_PLAYBOOK.md",
        "label": "Backup playbook — nightly pipeline, restore rehearsal, coverage",
        "media": "text/plain",
    },
    "codebase_audit": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_CODEBASE_AUDIT.md",
        "label": "Codebase audit — findings register and remediation status",
        "media": "text/plain",
    },
    "feature_flag_playbook": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_FEATURE_FLAG_PLAYBOOK.md",
        "label": "Feature-flag playbook — all seven gates and safe recipes",
        "media": "text/plain",
    },
    "time_pressure_readme": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_TIME_PRESSURE_README.md",
        "label": "TIME_PRESSURE_ENABLED — workload manipulation internals (v15) + flag-off nopress contract",
        "media": "text/plain",
    },
    "ai_qa_readme": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_AI_QA_README.md",
        "label": "AI_QA_ENABLED — optional rules-Q&A stage implementation",
        "media": "text/plain",
    },
    "ai_assistant_readme": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_AI_ASSISTANT_README.md",
        "label": "AI_ASSISTANT_ENABLED — assistant-pane gate implementation",
        "media": "text/plain",
    },
    "crate_distribution_readme": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_CRATE_DISTRIBUTION_README.md",
        "label": "CRATE_OVERLAP/CRATE_NUMBER_ENABLED — crate-distribution gate implementation",
        "media": "text/plain",
    },
    "bonus_payment_readme": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_BONUS_PAYMENT_README.md",
        "label": "BONUS_PAYMENT_ENABLED — no-bonus consent/payment artifact substitution",
        "media": "text/plain",
    },
    "e2e_smoke_playbook": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_E2E_SMOKE_TEST_PLAYBOOK.md",
        "label": "E2E smoke playbook — smoke ladder, drivers, verification queries",
        "media": "text/plain",
    },
    "risk_assessment": {
        "file": "INVESTIGATORS_TEAM_EXPERIMENT_RISK_ASSESSMENT.md",
        "label": "Risk assessment — top-complexity sections and mitigations",
        "media": "text/plain",
    },
    "platform_overview": {
        "file": "docs/PLATFORM_OVERVIEW.md",
        "label": "Platform overview — architecture, guarantees, and limits",
        "media": "text/plain",
    },
    "codegraph": {
        "file": ".codegraph/codegraph.db",
        "label": "CodeGraph index (SQLite) — investigators tree only",
        "media": "application/octet-stream",
    },
}


def _docs_root(request: Request) -> Path | None:
    raw = _settings(request).ADMIN_DOCS_DIR.strip()
    return Path(raw) if raw else None


@router.get("/admin/docs")
def admin_docs(request: Request) -> Any:
    _guard(request, "researcher")
    root = _docs_root(request)
    docs: list[dict[str, Any]] = []
    for doc_id, meta in _DOC_FILES.items():
        entry: dict[str, Any] = {"id": doc_id, "label": meta["label"], "file": meta["file"]}
        if root is not None:
            target = root / meta["file"]
            if target.is_file():
                stat = target.stat()
                entry["size"] = stat.st_size
                entry["modified_at"] = datetime.fromtimestamp(stat.st_mtime).isoformat(
                    timespec="seconds"
                )
        docs.append(entry)
    return {"docs": docs}


@router.get("/admin/docs/{doc_id}")
def admin_doc(request: Request, doc_id: str, download: bool = False) -> Any:
    _guard(request, "researcher")
    meta = _DOC_FILES.get(doc_id)
    root = _docs_root(request)
    if meta is None:
        return _problem(404, "doc_not_found", "no such document")
    if root is None:
        return _problem(503, "docs_unavailable", "docs root is not configured")
    target = root / meta["file"]
    if not target.is_file():
        return _problem(404, "doc_not_found", f"{meta['file']} is not available")
    return FileResponse(
        target,
        media_type=meta["media"],
        filename=meta["file"].rsplit("/", 1)[-1] if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


# ----- dashboard (WP17 Phase A) -------------------------------------------------------------
#
# Read-only analytic views over the shared cohort contract (plan §1). Every endpoint:
# - is researcher-gated (operator/compensation get 403),
# - runs in one REPEATABLE READ READ ONLY transaction (one snapshot per request),
# - answers with Cache-Control: no-store (research data never lands in a cache),
# - validates the shared filter set (study/batch/compatibility/mode/UTC window) → 422.


def _dash_conn(engine: Engine) -> Any:
    """Open a connection whose first statement declares the snapshot mode."""
    conn = engine.connect()
    conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    return conn


def _dash_filters(
    request: Request, *, allowed_extra: tuple[str, ...] = ()
) -> dash.DashboardFilter | JSONResponse:
    try:
        return dash.parse_filters(request.query_params, allowed_extra=allowed_extra)
    except dash.FilterError as exc:
        return _problem(422, "bad_filter", str(exc))


def _dash_ok(payload: Any) -> JSONResponse:
    return JSONResponse(adminviews.jsonable(payload), headers={"Cache-Control": "no-store"})


@router.get("/admin/dashboard/overview")
def admin_dash_overview(request: Request, recent_limit: int = 25) -> Any:
    _guard(request, "researcher")
    flt = _dash_filters(request, allowed_extra=("recent_limit",))
    if isinstance(flt, JSONResponse):
        return flt
    with _dash_conn(_engine(request)) as conn:
        return _dash_ok(
            dashboard_ops.overview(
                conn,
                flt,
                recent_limit=max(1, min(recent_limit, 100)),
                current_build=request.app.state.build.source_sha256,
            )
        )


@router.get("/admin/dashboard/readiness")
def admin_dash_readiness(request: Request) -> Any:
    _guard(request, "researcher")
    flt = _dash_filters(request, allowed_extra=("page_size", "offset", "audit_scope"))
    if isinstance(flt, JSONResponse):
        return flt
    try:
        page_size = dash.parse_page(request.query_params, default=50, maximum=200)
        offset = dash.parse_offset(request.query_params)
        audit_scope = dash.parse_audit_scope(request.query_params)
    except dash.FilterError as exc:
        return _problem(422, "bad_filter", str(exc))
    with _dash_conn(_engine(request)) as conn:
        return _dash_ok(
            dashboard_ops.readiness(
                conn,
                _store(request),
                flt,
                page_size=page_size,
                offset=offset,
                audit_scope=audit_scope,
                feature_flags=_settings(request).flags(),
            )
        )


@router.get("/admin/dashboard/timing")
def admin_dash_timing(request: Request) -> Any:
    _guard(request, "researcher")
    flt = _dash_filters(request)
    if isinstance(flt, JSONResponse):
        return flt
    with _dash_conn(_engine(request)) as conn:
        return _dash_ok(dashboard_ops.timing(conn, flt))


@router.get("/admin/dashboard/instruments")
def admin_dash_instruments(request: Request) -> Any:
    _guard(request, "researcher")
    flt = _dash_filters(request)
    if isinstance(flt, JSONResponse):
        return flt
    with _dash_conn(_engine(request)) as conn:
        return _dash_ok(dashboard_ops.instruments(conn, _store(request), flt))


@router.get("/admin/dashboard/analysis")
def admin_dash_analysis(request: Request) -> Any:
    _guard(request, "researcher")
    flt = _dash_filters(request)
    if isinstance(flt, JSONResponse):
        return flt
    with _dash_conn(_engine(request)) as conn:
        body = dashboard_analysis.analysis(
            conn,
            _store(request),
            flt,
            flags=_settings(request).flags(),
        )
    # AID-63 follow-up: the client hides scripted-mechanism cells in the
    # mechanism-arm splits while the flag is off — display only; historical
    # scripted sessions still pool into the cohort measures.
    body["scripted_teammates_enabled"] = _settings(request).SCRIPTED_TEAMMATES_ENABLED
    body["ai_assistant_enabled"] = _settings(request).AI_ASSISTANT_ENABLED
    return _dash_ok(body)


@router.post("/admin/dashboard/chat_judge")
async def admin_dash_chat_judge(request: Request) -> Any:
    """Enqueue an AI-judge verdict for a session — a wall-clock ``chat_judge``
    scheduled_job the worker runs against gemma4:26b; the verdict lands in
    ``admin_audit`` (action=chat_judge_verdict)."""
    principal = _guard(request, "researcher")
    body = await request.json()
    sid = _session_id(str(body.get("session_id") or ""))
    if isinstance(sid, JSONResponse):
        return sid
    question = str(body.get("question") or "").strip()[:2000] or (
        "Summarize how the participant used teammate input."
    )
    with _engine(request).begin() as conn:
        conn.execute(
            s.scheduled_job.insert().values(
                job_id=uuid.uuid4(),
                session_id=sid,
                logical_job_key=f"chat_judge:{uuid.uuid4()}",
                kind="chat_judge",
                clock_basis="wall",
                due_wall_at=db_now(conn),
                claimable_at=db_now(conn),
                payload={"question": question, "asked_by": principal.role},
                precondition={},
            )
        )
    return _dash_ok({"queued": True, "session_id": str(sid)})


@router.get("/admin/dashboard/chat_judge/{session_id}")
def admin_dash_chat_judge_list(request: Request, session_id: str) -> Any:
    """AI-judge verdicts for a session (newest first)."""
    _guard(request, "researcher")
    sid = _session_id(session_id)
    if isinstance(sid, JSONResponse):
        return sid
    with _dash_conn(_engine(request)) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                select(s.admin_audit.c.audit_id, s.admin_audit.c.payload,
                       s.admin_audit.c.occurred_at)
                .where(
                    s.admin_audit.c.session_id == sid,
                    s.admin_audit.c.action == "chat_judge_verdict",
                )
                .order_by(s.admin_audit.c.occurred_at.desc())
            ).mappings().all()
        ]
    return _dash_ok({"verdicts": rows})


@router.get("/admin/dashboard/chat")
def admin_dash_chat(request: Request) -> Any:
    """Scoped chat transcripts for the Team Chat tab — same filter set as
    ``/admin/dashboard/analysis``; participant bodies withheld unless the
    session's latest data-use choice is ``use_all``."""
    _guard(request, "researcher")
    flt = _dash_filters(request)
    if isinstance(flt, JSONResponse):
        return flt
    with _dash_conn(_engine(request)) as conn:
        body = dash.chat_transcripts(conn, flt)
    return _dash_ok(body)


class ExportTableBody(BaseModel):
    """One named research table plus the dashboard scope to download it under.
    Extra keys are forbidden (DA-25): a misspelled bound must never widen the
    cohort."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=64)
    scope: ExportScopeBody = Field(default_factory=ExportScopeBody)


@router.post("/admin/exports/table")
async def admin_export_table(request: Request) -> Any:
    """Per-table CSV download of the research-study export (INV-1106 definitions).

    POST because the download is a recorded data-egress event: it carries the
    mutation gate (origin + CSRF) like every audited write. Cohort resolution,
    data-use checks and row reads share one ``REPEATABLE READ READ ONLY``
    snapshot (DC-02/DC-03), so a withdrawal committed before the read is always
    honored. Declared free-text cells emit verbatim (2026-10-16 owner direction:
    no exportable file carries ``withheld_unreviewed``); sessions without an
    explicit ``use_all`` choice are still dropped from participant-text tables
    (DC-01). The audit row pins the body checksum —
    if it cannot be written, the CSV is not released.
    """
    principal = _guard(request, "researcher", mutate=True)
    body = await _body(request, ExportTableBody, "expected {name, scope}")
    name = body.name
    if name not in EXPORT_TABLE_NAMES:
        return _problem(422, "unknown_table", "name must be a study-export table")
    flt = _scope_filter(body.scope)
    if isinstance(flt, JSONResponse):
        return flt
    normalized = flt.normalized()
    engine = _engine(request)
    with _dash_conn(engine) as conn:
        sessions = dash.select_cohort(conn, flt)
        sids = dash.session_ids(sessions)
        du = dash.latest_data_use(conn, sids)
        allowed = [sid for sid in sids if du.get(sid) != "withdraw_all"]
        if not allowed:
            return _problem(
                422,
                "empty_scope",
                "the scope resolves to zero sessions — refusing to export an "
                "empty or unfiltered table",
            )
        if len(allowed) > MAX_EXPORT_SESSIONS:
            return _problem(
                422,
                "scope_too_large",
                f"scope resolves to {len(allowed)} sessions; bound is {MAX_EXPORT_SESSIONS}",
            )
        served = [
            sid
            for sid in allowed
            if name not in DOWNLOAD_FREE_TEXT_TABLES or du.get(sid) == USE_ALL
        ]
        srows = session_rows_for_ids(conn, served)
        settings = _settings(request)
        rows = download_table(
            conn,
            name,
            srows,
            normalized,
            data_use=du,
            feature_flags=settings.feature_flags(),
        )
        columns = table_columns(name, rows)
        columns.extend(c for c in flag_columns(settings.feature_flags()) if c not in columns)
        payload = csv_bytes(columns, rows)
    try:
        with engine.begin() as conn:
            adminops.audit(
                conn,
                actor=principal.actor,
                action="export_table",
                payload={
                    "table": name,
                    "scope": normalized,
                    "n_sessions": len(served),
                    "n_scope": len(allowed),
                    "rows": len(rows),
                    "sha256": sha256_bytes(payload),
                },
            )
    except DBAPIError:
        return _problem(503, "audit_unavailable", "download audit could not be recorded")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="investigators_{name}_{stamp}.csv"',
            "Cache-Control": "no-store",
            "X-Export-Rows": str(len(rows)),
            "X-Export-Sessions": str(len(served)),
        },
    )


@router.get("/admin/exports/tables")
def admin_export_tables(request: Request) -> Any:
    """The research-export table vocabulary — powers the per-table download grid."""
    _guard(request, "researcher")
    return _dash_ok({"tables": sorted(EXPORT_TABLE_NAMES)})


@router.get("/admin/exports")
def admin_export_catalog(request: Request) -> Any:
    """Published export history (INV-1700/§3.9): lists manifest sidecars under
    ADMIN_EXPORT_DIR; summary-only when the dir is unset. Takes no query params
    — unknown keys are rejected rather than silently ignored (DA-25)."""
    _guard(request, "researcher")
    if request.query_params:
        return _problem(
            422,
            "bad_filter",
            f"unsupported parameter(s): {', '.join(sorted(request.query_params))}",
        )
    return _dash_ok(dashboard_exports.catalog(_settings(request).ADMIN_EXPORT_DIR.strip()))


__all__ = ["router"]


@router.get("/admin/assistant-monitor")
def admin_assistant_monitor(request: Request) -> Any:
    _guard(request, "researcher", "operator")
    flt = _dash_filters(request)
    if isinstance(flt, JSONResponse):
        return flt
    from app.services.assistant_monitor import monitor

    with _dash_conn(_engine(request)) as conn:
        return _dash_ok(
            monitor(
                conn,
                dash.select_cohort(conn, flt),
                current_build=request.app.state.build.source_sha256,
            )
        )
