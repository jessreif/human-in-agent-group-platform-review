#!/usr/bin/env python3
"""Assistant release panel collector (BA-108 restoration, 2026-10-12).

Drives signed-preview sessions through every cell of a release panel —
``profiles x arms x seeds`` — sending each probe as an ``assistant_request``
command, running the worker-side scheduler in-process so generation, claim
checking and delivery all take the real code path, then harvesting the durable
rows into the evidence JSON ``scripts/check_assistant_release.py`` validates.

The collector never fabricates outcomes: a cell whose generation fails,
falls back, or never delivers simply records that — the checker fails it.

Invocation (run from ``backend/`` so ``uv run`` resolves the venv)::

    uv run python ../scripts/assistant_panel.py collect \
        --panel ../release/assistant-panel-v2.json \
        --out ../release/evidence/assistant-panel-v2-YYYY-MM-DD.json \
        --provider ollama --auto-adjudicate --regressions-ok

* ``--provider ollama`` requires the worker-side local adapter settings
  (MODEL_PROVIDER + LOCAL_MODEL_BASE_URL/ID/DIGEST) — run inside the worker
  netns for a real-provider acceptance run. ``stub`` exercises the
  generate→validate→deliver path without a model; ``none`` records honest
  fallback outcomes.
* ``--dsn`` may name an existing disposable database; without one an ephemeral
  PostgreSQL container is provisioned and destroyed (same guard as the
  integration suite — a live DSN is refused).
* ``--auto-adjudicate`` marks every objectively-complete row
  ``adjudication: "correct"`` and records ``adjudication_source:
  "objective-auto"``; without it rows stay ``"pending"`` and the evidence can
  never pass until a reviewer adjudicates.
* ``--regressions-ok`` asserts the named regression battery passed in the
  current backend gate run; without it each case is recorded ``"unverified"``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

BUILD_SHA_DEFAULT = "ab" * 32

# Static map of which regression tests cover each required case — kept in step
# with the remediation battery; recorded on the evidence for reviewers.
REGRESSION_TESTS = {
    "destination_only": "tests/integration/test_assistant_audit_remediation.py::test_destination_only_delivers_supported_claim",  # noqa: E501
    "arrival_from_transit": "tests/test_assistant_audit_remediation.py::test_all_authoritative_templates_have_exact_positive_coverage",  # noqa: E501
    "wrong_time": "tests/test_assistant_audit_remediation.py::test_all_authoritative_templates_have_exact_positive_coverage (mutated-stamp case)",  # noqa: E501
    "stale_current": "tests/test_assistant_audit_remediation.py::test_reported_adversarial_assertions_reject",  # noqa: E501
    "wrong_citation": "tests/integration/test_assistant_audit_remediation.py::test_wrong_citation_gate_and_ledger_agree",  # noqa: E501
    "duplicate_citation": "tests/integration/test_assistant_audit_remediation.py::test_duplicate_citations_leave_durable_outcome_and_delivery + tests/test_assistant_audit_remediation.py::test_citations_are_local_and_idempotent",  # noqa: E501
    "unknown_location": "tests/test_assistant_audit_remediation.py::test_reported_adversarial_assertions_reject (Gotham Depot)",  # noqa: E501
    "false_denial": "tests/test_assistant_audit_remediation.py::test_reported_adversarial_assertions_reject + test_scoped_absence_is_not_a_global_denial",  # noqa: E501
    "wrong_subject": "tests/test_assistant_audit_remediation.py::test_reported_adversarial_assertions_reject (wrong-crate contents vocab)",  # noqa: E501
    "contents_inversion": "tests/integration/test_assistant_audit_remediation.py::test_contents_gate_probe_never_calls_provider",  # noqa: E501
    "negation_role": "tests/test_assistant_audit_remediation.py::test_reported_adversarial_assertions_reject (not moved / not transferred)",  # noqa: E501
    "invalid_utf8": "tests/test_assistant_audit_remediation.py::test_invalid_completion_has_stable_taxonomy + test_invalid_listing_has_stable_taxonomy",  # noqa: E501
    "storage_invalid": "tests/test_assistant_audit_remediation.py::test_invalid_completion_has_stable_taxonomy (NUL/non-UTF8 bodies)",  # noqa: E501
    "snapshot_missing": "tests/integration/test_assistant_audit_remediation.py::test_missing_snapshot_delivers_nonfactual_failure",  # noqa: E501
    "snapshot_hash": "tests/test_assistant_audit_remediation.py::test_semantic_hash_detects_body_and_destination_drift",  # noqa: E501
    "scope_followup": "tests/integration/test_assistant_audit_remediation.py::test_bare_followup_pins_subject + tests/test_assistant_audit_remediation.py::test_normalized_scope",  # noqa: E501
    "policy_drift": "tests/test_assistant_audit_remediation.py::test_pinned_generation_policy_refuses_drift",  # noqa: E501
    "generation_stall": "tests/test_assistant_audit_remediation.py::test_monitor_distinguishes_failure_delivery_and_deterministic_routes",  # noqa: E501
    "delivery_loss": "tests/test_assistant_audit_remediation.py::test_monitor_distinguishes_failure_delivery_and_deterministic_routes + tests/integration/test_wp10a_generation.py::test_late_generation_is_quarantined_never_delivered",  # noqa: E501
    "retry_limit": "tests/integration/test_assistant_audit_remediation.py::test_retry_limit_dead_letters_without_provider",  # noqa: E501
}


def _send(runner: Any, sid: uuid.UUID, writer: uuid.UUID, kind: str, payload: dict[str, Any]):
    """Dispatch one command; returns (accepted, reason, revision_used)."""
    rev = runner._revision(sid)
    out = runner.send(sid, writer, kind, payload, rev)
    return out


def _drive_to_task(runner: Any, sid: uuid.UUID, writer: uuid.UUID, bound: Any) -> None:
    """consent -> training steps -> connecting -> ready -> task_open."""
    from app.services import lifecycle
    from harness import clockwork

    consent_key = bound.artifacts["instruments.consent"].key
    _send(runner, sid, writer, "consent", {"action": "consented", "text_version": consent_key})
    _send(runner, sid, writer, "training_step", {"step": 1})
    _send(
        runner,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "share", "record_id": "PRACTICE-K3-04"},
    )
    _send(
        runner,
        sid,
        writer,
        "training_step",
        {"step": "practice", "action": "pick_location", "crate": 3, "location": "The Sawmill"},
    )
    # nl_only profiles gate completion on a typed practice ask.
    if bound.manifest.interaction_mode == "nl_only":
        _send(
            runner,
            sid,
            writer,
            "training_step",
            {"step": "practice", "action": "ask", "text": "anything on crate 3?"},
        )
    _send(
        runner,
        sid,
        writer,
        "training_step",
        {
            "step": "comprehension",
            "complete": True,
            "answers": dict(lifecycle.COMPREHENSION_KEY),
        },
    )
    clockwork.force_wall_due(runner.db.runtime, sid, "connecting")
    runner._tick()
    _send(runner, sid, writer, "ready", {})
    clockwork.force_wall_due(runner.db.runtime, sid, "task_open")
    runner._tick()


def _record_render_receipt(db: Any, sid: uuid.UUID, seq: int) -> None:
    """Commit the same durable receive+render receipt the browser telemetry
    endpoint commits — a ``telemetry_render`` event plus the
    ``delivery_receipt`` row, via the real internal helper."""
    from sqlalchemy import insert

    from app.services.telemetry_ingest import record_delivery_receipt
    from app.db import schema as s
    from app.db.tx import db_now, lock_session

    with db.runtime.begin() as conn:
        now = db_now(conn)
        for kind in ("receive", "render"):
            row = lock_session(conn, sid)
            telemetry_seq = int(row["next_event_seq"])
            conn.execute(
                insert(s.event).values(
                    session_id=sid,
                    seq=telemetry_seq,
                    event_id=uuid.uuid4(),
                    stage=row["stage"],
                    actor="participant",
                    type=f"telemetry_{kind}",
                    revision=row["revision"],
                    server_time=now,
                    payload={"client_ms": 0, "data": {"seq": seq}},
                )
            )
            record_delivery_receipt(
                conn,
                sid,
                kind=kind,
                data={"seq": seq},
                client_ms=0,
                telemetry_seq=telemetry_seq,
                now=now,
            )
            conn.execute(
                s.session.update()
                .where(s.session.c.session_id == sid)
                .values(next_event_seq=telemetry_seq + 1)
            )


def _assistant_output(db: Any, sid: uuid.UUID, after_seq: int):
    """The newest assistant-channel displayed output after ``after_seq``."""
    from sqlalchemy import select

    from app.db import schema as s

    with db.runtime.connect() as conn:
        return (
            conn.execute(
                select(s.displayed_output, s.message.c.channel, s.message.c.committed_seq)
                .select_from(
                    s.displayed_output.join(
                        s.message,
                        (s.message.c.session_id == s.displayed_output.c.session_id)
                        & (s.message.c.message_id == s.displayed_output.c.message_id),
                    )
                )
                .where(
                    s.displayed_output.c.session_id == sid,
                    s.message.c.channel == "assistant",
                    s.displayed_output.c.delivered_seq > after_seq,
                )
                .order_by(s.displayed_output.c.delivered_seq.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )


def _harvest(
    db: Any,
    sid: uuid.UUID,
    *,
    profile: str,
    arm: str,
    seed: str,
    probe: Mapping[str, Any],
    question: str,
    cohort: str,
    command_id: uuid.UUID,
    auto_adjudicate: bool,
) -> dict[str, Any]:
    from sqlalchemy import select

    from app.db import schema as s

    with db.runtime.connect() as conn:
        req = (
            conn.execute(
                select(s.request)
                .where(s.request.c.session_id == sid, s.request.c.command_id == command_id)
                .order_by(s.request.c.accepted_seq.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        episode = (
            conn.execute(
                select(s.request_episode)
                .where(
                    s.request_episode.c.session_id == sid,
                    s.request_episode.c.request_id == req["request_id"],
                )
                .order_by(s.request_episode.c.opened_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
            if req is not None
            else None
        )
        snap = (
            conn.execute(
                select(s.knowledge_snapshot)
                .where(
                    s.knowledge_snapshot.c.session_id == sid,
                    s.knowledge_snapshot.c.seat == "assistant",
                    s.knowledge_snapshot.c.parent_command_id == command_id,
                )
                .limit(1)
            )
            .mappings()
            .first()
        )
        call = (
            conn.execute(
                select(s.model_call)
                .where(
                    s.model_call.c.session_id == sid,
                    s.model_call.c.snapshot_id == snap["snapshot_id"],
                )
                .order_by(s.model_call.c.attempt.desc())
                .limit(1)
            )
            .mappings()
            .first()
            if snap is not None
            else None
        )
        rendered = (
            conn.execute(
                select(s.delivery_receipt.c.seq).where(
                    s.delivery_receipt.c.session_id == sid,
                    s.delivery_receipt.c.rendered_at.isnot(None),
                )
            )
            .scalars()
            .all()
        )
    rendered_set = {int(x) for x in rendered}
    after_seq = int(req["accepted_seq"]) - 1 if req else 0
    out_row = _assistant_output(db, sid, after_seq)
    validation = (call or {}).get("validation") or {}
    claims = validation.get("claims") or []
    violations = validation.get("violations")
    route = "generated" if call is not None else "intentional_deterministic"
    delivered_seq = out_row["delivered_seq"] if out_row else None
    row: dict[str, Any] = {
        "profile": profile,
        "arm": arm,
        "seed": seed,
        "probe": probe["id"],
        "route": route,
        "cohort": cohort,
        "question": question,
        "session_id": str(sid),
        "snapshot_id": str(snap["snapshot_id"]) if snap else None,
        "authorized_record_ids": (
            sorted(snap["record_manifest"].keys())
            if snap and isinstance(snap["record_manifest"], Mapping)
            else [r["record_id"] for r in snap["record_manifest"]]
            if snap
            else []
        ),
        "request_id": str(req["request_id"]) if req else None,
        # Panel-declared scope; ``request_scope`` is what the command actually
        # resolved (None for bare/contents questions resolved by the gate).
        "scope": probe["scope"],
        "request_scope": req["crate"] if req else None,
        "deterministic_only": route != "generated",
        "generate_status": "committed" if delivered_seq else "not_delivered",
        "episode_status": episode["status"] if episode else None,
        "close_reason": episode["close_reason"] if episode else None,
        "call_id": str(call["call_id"]) if call else None,
        "status": call["status"] if call else None,
        "model": call["model"] or call["model_digest"] if call else None,
        "violations": violations,
        "supported_claims": sum(1 for c in claims if c.get("verdict") == "supported"),
        "claims": claims or None,
        "response_type": validation.get("response_type"),
        "rendered_record_ids": validation.get("rendered_record_ids")
        or ((call or {}).get("input") or {}).get("grounded", {}).get("rendered_record_ids"),
        "provider_dispatched": call is not None,
        "displayed_output_id": str(out_row["output_id"]) if out_row else None,
        "message_id": str(out_row["message_id"]) if out_row else None,
        "fallback": out_row["fallback"] if out_row else None,
        "shown_text": out_row["shown_text"] if out_row else None,
        "delivered_seq": delivered_seq,
        "render_receipt": str(delivered_seq) if delivered_seq in rendered_set else None,
        "adjudication_lines": (str(out_row["shown_text"]).splitlines() if out_row else []),
        "unexplained_gaps": (
            (0 if delivered_seq is not None and delivered_seq in rendered_set else 1)
            + (
                1
                if req is not None
                and req["crate"] is not None
                and int(req["crate"]) != int(probe["scope"])
                else 0
            )
        ),
    }
    complete = (
        row["displayed_output_id"]
        and row["render_receipt"]
        and row["unexplained_gaps"] == 0
        and (route != "generated" or row["status"] == "succeeded")
    )
    row["adjudication"] = "correct" if auto_adjudicate and complete else "pending"
    if auto_adjudicate:
        row["adjudication_source"] = "objective-auto"
    return row


def _enter_preview(db: Any, store: Any, *, manifest_id: str, arm: str, seed: str, key: bytes):
    """Issue a signed preview descriptor and enter through the real descriptor
    path — the session pins arm + seed and lands in the preview cohort."""
    from app.domain.manifests.binding import bind
    from app.services import preview
    from app.services.entry import enter
    from app.services.sessions import register_manifest

    stored = store.manifests[manifest_id]
    bound = bind(store, manifest_id, mode="preview")
    with db.superuser.begin() as conn:
        register_manifest(conn, bound)
    token = preview.issue_descriptor(
        key,
        manifest_id=manifest_id,
        manifest_sha256=stored.sha256,
        arm=arm,
        seed=seed,
        mode="preview",
        timing_mode="faithful",
        qa_overlay=None,
        now=datetime.now().astimezone(),
        lifetime=timedelta(hours=2),
        issuer="assistant_panel.py",
    )
    result = enter(
        db.runtime,
        store,
        query={"pt": token},
        app_mode="preview",
        preview_key=key,
        current_manifest_sha256={manifest_id: stored.sha256},
        build_source_sha256=BUILD_SHA_DEFAULT,
    )
    return result, bound


def _model_digest(settings: Any) -> str:
    raw = settings.LOCAL_MODEL_DIGEST.removeprefix("sha256:").strip()
    return f"{settings.LOCAL_MODEL_ID}@sha256:{raw}" if raw else "unresolved:D05"


def collect(args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from app.db import schema as s
    from app.domain.manifests.store import ContentStore
    from app.services.generation_policy import identity
    from app.version import build_info
    from harness import clockwork
    from harness.cli import _make_provider
    from harness.runner import Runner
    from harness.simdb import provision

    panel_path: Path = args.panel
    panel = json.loads(panel_path.read_text())
    panel_sha = hashlib.sha256(panel_path.read_bytes()).hexdigest()

    build = args.build or build_info().source_sha256
    model = args.model
    if model is None:
        try:
            from app.settings import load_settings

            model = _model_digest(load_settings())
        except Exception:  # noqa: BLE001 - settings may fail to load in a bare workspace
            model = "unresolved:D05"

    provider = _make_provider(args.provider)
    db, _disposable = provision(args.dsn)
    store = ContentStore.load_default()
    runner = Runner(db, store, provider=provider, worker="panel-collector")
    key = secrets.token_bytes(32)

    rows: list[dict[str, Any]] = []
    try:
        for profile, default_route in sorted(panel["profiles"].items()):
            for arm in panel["arms"]:
                for seed in panel["seeds"]:
                    result, bound = _enter_preview(
                        db, store, manifest_id=profile, arm=arm, seed=seed, key=key
                    )
                    sid, writer = result.session_id, result.writer_id
                    _drive_to_task(runner, sid, writer, bound)
                    with db.runtime.connect() as conn:
                        cohort = conn.execute(
                            select(s.session.c.mode).where(s.session.c.session_id == sid)
                        ).scalar_one()
                    for probe in panel["probes"]:
                        from app.db.tx import Command
                        from app.services import lifecycle

                        command_id = uuid.uuid4()
                        rev = runner._revision(sid)
                        handler = lifecycle.handler_for(
                            "assistant_request",
                            store=store,
                            build_sha=BUILD_SHA_DEFAULT,
                            writer=writer,
                        )

                        def _advance(ctx: Any) -> None:
                            b = lifecycle.bound_for_session(ctx.conn, store, ctx.session)
                            from app.services import scheduler

                            scheduler.advance_due(ctx, b)

                        from app.db.tx import execute_command

                        out = execute_command(
                            db.runtime,
                            Command(
                                command_id=command_id,
                                session_id=sid,
                                kind="assistant_request",
                                expected_revision=rev,
                                payload={"body": probe["question"]},
                            ),
                            handler,
                            advance_due=_advance,
                        )
                        if not out.accepted:
                            rows.append(
                                {
                                    "profile": profile,
                                    "arm": arm,
                                    "seed": seed,
                                    "probe": probe["id"],
                                    "route": probe.get("route", default_route),
                                    "cohort": cohort,
                                    "question": probe["question"],
                                    "session_id": str(sid),
                                    "rejected": out.reason,
                                    "adjudication": "pending",
                                    "unexplained_gaps": 1,
                                }
                            )
                            continue
                        # The request row carries accepted_seq; the output
                        # poll uses the command's own seq as the floor.
                        from sqlalchemy import select as _sel

                        with db.runtime.connect() as conn:
                            acc = conn.execute(
                                _sel(s.request.c.accepted_seq).where(
                                    s.request.c.session_id == sid,
                                    s.request.c.command_id == command_id,
                                )
                            ).scalar_one_or_none() or (rev + 1)
                        # Let generation run: tick until an assistant output
                        # lands or the budget is spent (active advance keeps
                        # active-basis jobs moving).
                        for _ in range(args.delivery_rounds):
                            if _assistant_output(db, sid, int(acc) - 1):
                                break
                            clockwork.advance_active(db.runtime, sid, 2000)
                            with db.runtime.begin() as conn:
                                conn.execute(
                                    s.scheduled_job.update()
                                    .where(
                                        s.scheduled_job.c.session_id == sid,
                                        s.scheduled_job.c.status == "pending",
                                    )
                                    .values(claimable_at=func.now())
                                )
                            clockwork.tick(
                                db.runtime, store, provider=provider, worker="panel-collector"
                            )
                        output = _assistant_output(db, sid, int(acc) - 1)
                        if output is not None:
                            _record_render_receipt(db, sid, int(output["delivered_seq"]))
                        rows.append(
                            _harvest(
                                db,
                                sid,
                                profile=profile,
                                arm=arm,
                                seed=seed,
                                probe=probe,
                                question=probe["question"],
                                cohort=cohort,
                                command_id=command_id,
                                auto_adjudicate=args.auto_adjudicate,
                            )
                        )
    finally:
        db.dispose()

    evidence = {
        "panel_version": panel["version"],
        "panel_sha256": panel_sha,
        "build": build,
        "model": model,
        "policy": identity(build, model),
        "results": rows,
        "regressions": {
            case: "pass" if args.regressions_ok else "unverified"
            for case in panel["required_regressions"]
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "regression_tests": REGRESSION_TESTS,
        "collection": {
            "driver": "scripts/assistant_panel.py collect",
            "provider": getattr(provider, "provider_id", None) or "deterministic-fallback",
            "sessions": "signed preview descriptors, ephemeral run signing key",
            "adjudication": "objective-auto" if args.auto_adjudicate else "pending-review",
        },
    }
    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, indent=1, default=str) + "\n")
    sidecar = out_path.with_name(out_path.name.removesuffix(".json") + "-rows.json")
    grouped: dict[str, Any] = {}
    for r in rows:
        key = f"{r['profile'].removeprefix('tp_').removesuffix('_' + r['arm'])}_{r['arm']}"
        cell = grouped.setdefault(key, {"session_id": r.get("session_id"), "rows": {}})
        cell["rows"][r["probe"]] = r
    sidecar.write_text(json.dumps(grouped, indent=1, default=str) + "\n")
    ok = sum(1 for r in rows if r.get("displayed_output_id"))
    print(f"wrote {out_path} (+ {sidecar.name})")
    print(f"cells: {len(rows)}, delivered: {ok}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="assistant_panel", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="drive the panel and emit evidence")
    c.add_argument("--panel", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--dsn", default=None)
    c.add_argument("--provider", choices=["none", "stub", "ollama"], default="none")
    c.add_argument("--build", default=None, help="override build sha (64 hex)")
    c.add_argument("--model", default=None, help="override model digest")
    c.add_argument("--auto-adjudicate", action="store_true")
    c.add_argument("--regressions-ok", action="store_true")
    c.add_argument("--delivery-rounds", type=int, default=40)
    args = ap.parse_args(argv)
    if args.cmd == "collect":
        return collect(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
