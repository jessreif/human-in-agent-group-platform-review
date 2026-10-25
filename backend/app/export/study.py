"""Consistent-snapshot study export (INV-1106, spec §§9.1-9.3, §9.5).

One ``REPEATABLE READ`` transaction produces the whole export, so every table is a read of
the same committed snapshot (spec §9.2: "generated from committed data at a consistent
snapshot"). The ``identity`` schema is never read — research exports contain no platform
identifiers (spec §8.1); the compensation product lives in :mod:`app.export.restricted`.

Tables produced:

- ``participant_wide`` — one row per session keyed by ``research_pid``/``session_id``:
  study, arm, every version stamp (manifest/bundle/binding/build, compatibility_key,
  schema/metric versions), exposure/completion/technical status, behavioral summaries and
  instrument coverage. Legacy ``inv_*`` equivalents are named ``inv_*`` only where the
  INV-1107 reconciliation says the meaning matches; the mislabeled legacy
  ``inv_first_request_sec`` is exported as ``legacy_first_action_sec``.
- ``crate_long`` — one row per session x crate: assignment/role, initial cutoff, truth
  (authorized research products only; ``truth_provenance='simulated'``), final answer,
  correctness, confidence and evidence-transfer acquisition history.
- passthrough record tables — ``event``, ``message``, ``request``, ``request_episode``
  (episodes), ``share``, ``delivery_receipt``, ``model_call``, ``displayed_output``,
  ``knowledge_snapshot`` (context), ``claim``, ``claim_source`` (evidence),
  ``answer_revision``/``answer_final`` (revisions), ``confidence_response``,
  ``item_response``, ``manipulation_exposure`` (exposure), ``incident_interval``
  (incidents), ``scheduled_job``, ``command_receipt``, ``stage_transition``,
  ``clock_interval``, ``consent_event``, ``result``, ``data_use_choice``, ``world_snapshot``,
  ``record``, ``seat_holding``, ``file_membership``, ``draw_log``, ``agent_state``,
  plus ``experiment_manifest``/``batch`` for the selected sessions' versions.
- ``model_audit`` — model_call x displayed_output x knowledge_snapshot join (spec §9.2
  "model audit table … restricted raw text separately"): ``input``/``raw_response`` are
  never in this table.
- ``evidence_transfer`` — per record the transfer-ledger stages reached (held, eligible,
  selected, delivered, rendered, in_file, in_context, referenced, never) so missing
  coverage cannot silently become a zero-valued measure (spec §9.5).
- ``fidelity_rate`` — empty/fallback/late/suppressed/failed/off-schedule rates by arm,
  source, model and policy, grouped by compatibility_key (INV-1106/1108).
- ``incident_summary`` — descriptive technical-incident aggregates by compatibility_key x
  clock_effect x kind (INV-1108).

Provenance labels used throughout (spec §9.2/§9.3): ``configured`` (manifest/policy),
``simulated`` (seeded world generator), ``predicted`` (model output), ``observed``
(committed session records). Every table's manifest entry carries schema_version,
metric_version where the table contains computed measures, the filter set, row count and
checksums.
"""

from __future__ import annotations

import functools
import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine

from app.db import schema as s
from app.db.export import SESSION_TABLES
from app.db.vocabulary import USE_ALL
from app.export.aggregates import fidelity_rates, incident_summary
from app.export.csvsafe import (
    EXPORT_FILE_MODE,
    canonical_checksum,
    columns_of,
    export_dir,
    write_csv,
)
from app.measurement.evidence import transfer_summary
from app.measurement.fidelity import teammate_fidelity
from app.measurement.telemetry import (
    attention_measures,
    file_telemetry_measures,
    item_dwell_ms,
    item_timing,
    per_record_file_stats,
    record_return_latency,
)
from app.services.claims import transfer_ledger

EXPORT_SCHEMA_VERSION = "wp11-export/4"

# Feature-flag stamp (2026-10-15): every exported row carries the deployment's
# feature-gate posture as ``flag_<name>`` columns so any single CSV is
# self-describing for analysis; ``manifest.json`` records the same block.
# These are export-time deployment values — per-session provenance lives in
# ``flags_at_entry`` on the session/participant_wide rows (``session_opened``
# payload; sessions that predate flag recording carry null there).
FLAG_COLUMN_PREFIX = "flag_"


def flag_columns(flags: Mapping[str, bool]) -> dict[str, bool]:
    return {f"{FLAG_COLUMN_PREFIX}{name}": value for name, value in sorted(flags.items())}


METRIC_VERSION = "metrics.wp11/2"
MANIFEST_SCHEMA = "investigators.export_manifest/1"

# Passthrough session-owned tables -> export name. Payment-domain tables
# (payment_entitlement, completion_receipt) are excluded: they belong to the restricted
# compensation product and to nothing else (spec §9.2).
PASSTHROUGH = {
    "command_receipt": "command_receipt",
    "event": "event",
    "outbox": "outbox",
    "message": "message",
    "request": "request",
    "request_episode": "request_episode",
    "share": "share",
    "delivery_receipt": "delivery_receipt",
    "model_call": "model_call",
    "displayed_output": "displayed_output",
    "knowledge_snapshot": "context_snapshot",
    "claim": "claim",
    "claim_source": "claim_source",
    "answer_revision": "answer_revision",
    "answer_final": "answer_final",
    "confidence_response": "confidence_response",
    "item_response": "item_response",
    # AI_QA_ENABLED: rules-Q&A exchanges — participant free text (question) +
    # the answering model/fallback output, exported verbatim like chat_text.
    "qa_exchange": "qa_exchange",
    "manipulation_exposure": "exposure",
    "incident_interval": "incident_interval",
    "scheduled_job": "scheduled_job",
    "stage_transition": "stage_transition",
    "clock_interval": "clock_interval",
    "consent_event": "consent_event",
    "result": "result",
    "data_use_choice": "data_use_choice",
    "world_snapshot": "world_snapshot",
    "record": "record",
    "seat_holding": "seat_holding",
    "file_membership": "file_membership",
    "draw_log": "draw_log",
    "agent_state": "agent_state",
}
_RESTRICTED_SESSION_TABLES = {"payment_entitlement", "completion_receipt"}

PROVENANCE_LABELS = {
    "configured": "declared by the immutable manifest/resolved policy",
    "simulated": "produced by the seeded world/schedule generator",
    "predicted": "model or provider output",
    "observed": "committed session record",
}


@dataclass
class ExportTable:
    name: str
    rows: list[dict[str, Any]]
    description: str
    schema_version: str = EXPORT_SCHEMA_VERSION
    metric_version: str | None = None

    @property
    def checksum(self) -> str:
        return canonical_checksum(self.rows)


@dataclass
class StudyExport:
    kind: str
    snapshot_time: datetime
    filters: dict[str, Any]
    tables: dict[str, ExportTable] = field(default_factory=dict)
    feature_flags: dict[str, bool] = field(default_factory=dict)

    def add(self, table: ExportTable) -> None:
        self.tables[table.name] = table


def _rows(conn: Connection, table: Any, session_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    if not session_ids:
        return []
    result = conn.execute(select(table).where(table.c.session_id.in_(session_ids))).mappings().all()
    return [dict(r) for r in result]


def _sessions(conn: Connection, filters: dict[str, Any]) -> list[dict[str, Any]]:
    q = (
        select(s.session)
        .where(
            # A tombstoned session is deleted by contract (INV-1307); its surviving
            # append-only rows are the purge work order, not exportable data.
            ~select(s.admin_audit.c.session_id)
            .where(
                s.admin_audit.c.session_id == s.session.c.session_id,
                s.admin_audit.c.action == "deletion_tombstone",
            )
            .exists()
        )
        .order_by(s.session.c.created_at, s.session.c.session_id)
    )
    if filters.get("batch_id"):
        q = q.where(s.session.c.batch_id == filters["batch_id"])
    if filters.get("study_key"):
        q = q.where(s.session.c.study_key == filters["study_key"])
    # An *explicit* empty session_ids list is an empty selection, not "no
    # filter" (DA-10) — ``in_([])`` matches nothing rather than everything.
    # ``None`` (unset) leaves the cohort unrestricted.
    if filters.get("session_ids") is not None:
        q = q.where(s.session.c.session_id.in_(filters["session_ids"]))
    return [dict(r) for r in conn.execute(q).mappings().all()]


def _participant_wide(conn: Connection, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derived per-session measures from committed records only (spec §9.1)."""
    session_ids = [r["session_id"] for r in sessions]
    if not session_ids:
        return []
    pids = {
        str(r["participant_id"]): r["research_pid"]
        for r in conn.execute(
            select(s.participant.c.participant_id, s.participant.c.research_pid).where(
                s.participant.c.participant_id.in_([r["participant_id"] for r in sessions])
            )
        )
        .mappings()
        .all()
    }
    receipts = _rows(conn, s.command_receipt, session_ids)
    events = _rows(conn, s.event, session_ids)
    shares = _rows(conn, s.share, session_ids)
    requests = _rows(conn, s.request, session_ids)
    messages = _rows(conn, s.message, session_ids)
    fidelity = {
        str(row["session_id"]): row
        for row in teammate_fidelity(
            sessions,
            [
                dict(r)
                for r in conn.execute(
                    select(s.experiment_manifest).where(
                        s.experiment_manifest.c.manifest_id.in_(
                            {row["manifest_id"] for row in sessions}
                        )
                    )
                ).mappings()
            ],
            _rows(conn, s.model_call, session_ids),
            messages,
            _rows(conn, s.displayed_output, session_ids),
        )
    }
    records = _rows(conn, s.record, session_ids)
    # 2026-10-15 crate-condition factors (audit E01): snapshot-first —
    # persisted ``world_params`` is the administered shape; the registered
    # manifest's task is the pre-world fallback; legacy defaults last.
    manifests = {
        r["manifest_id"]: r["content"]
        for r in conn.execute(
            select(s.experiment_manifest.c.manifest_id, s.experiment_manifest.c.content).where(
                s.experiment_manifest.c.manifest_id.in_({row["manifest_id"] for row in sessions})
            )
        ).mappings()
    }
    world_params = {
        str(r["session_id"]): (r["content"] or {}).get("world_params") or {}
        for r in conn.execute(
            select(s.world_snapshot.c.session_id, s.world_snapshot.c.content).where(
                s.world_snapshot.c.session_id.in_(session_ids)
            )
        ).mappings()
    }

    def task_shape(sess: dict[str, Any]) -> tuple[str, int, int | None]:
        params = world_params.get(str(sess["session_id"])) or {}
        if params.get("n_crates") is not None:
            return (
                params.get("crate_overlap") or "independent",
                int(params["n_crates"]),
                params.get("participant_intel_budget"),
            )
        task = (manifests.get(sess["manifest_id"]) or {}).get("task") or {}
        if task.get("n_crates") is not None:
            return (
                task.get("crate_overlap") or "independent",
                int(task["n_crates"]),
                task.get("participant_intel_budget"),
            )
        return "independent", 8, None

    exposures = _rows(conn, s.manipulation_exposure, session_ids)
    items = _rows(conn, s.item_response, session_ids)
    added = _rows(conn, s.file_membership, session_ids)
    results = {str(r["session_id"]): r for r in _rows(conn, s.result, session_ids)}
    record_crate = {(str(r["session_id"]), r["record_id"]): r["crate"] for r in records}
    events_by_command: dict[tuple[str, str], dict[str, Any]] = {}
    events_by_seq: dict[tuple[str, int], dict[str, Any]] = {}
    for ev in events:
        if ev.get("command_id") is not None:
            events_by_command[(str(ev["session_id"]), str(ev["command_id"]))] = ev
        events_by_seq[(str(ev["session_id"]), int(ev["seq"]))] = ev

    def first_active_ms(session_id: Any, kinds: tuple[str, ...]) -> int | None:
        best: int | None = None
        for rec in receipts:
            if (
                str(rec["session_id"]) != str(session_id)
                or rec["kind"] not in kinds
                or rec["outcome"] != "accepted"
            ):
                continue
            ev = events_by_command.get((str(session_id), str(rec["command_id"])))
            active = ev.get("active_ms") if ev else None
            if active is not None and (best is None or int(active) < best):
                best = int(active)
        return best

    out: list[dict[str, Any]] = []
    for sess in sessions:
        sid = str(sess["session_id"])
        my_receipts = [
            r for r in receipts if str(r["session_id"]) == sid and r["outcome"] == "accepted"
        ]
        my_shares = [
            r for r in shares if str(r["session_id"]) == sid and r["sharer"] == "participant"
        ]
        my_requests = [r for r in requests if str(r["session_id"]) == sid]
        my_messages = [r for r in messages if str(r["session_id"]) == sid]
        my_events = [e for e in events if str(e["session_id"]) == sid]
        my_exposures = [e for e in exposures if str(e["session_id"]) == sid]
        my_items = [i for i in items if str(i["session_id"]) == sid]
        my_added = [
            f for f in added if str(f["session_id"]) == sid and f["source"] == "added_from_team"
        ]
        checkin = next((e for e in my_exposures if e["kind"] == "pressure_checkin"), None)
        # Strict proactive sharing (spec §9.1): participant share of a record whose crate had
        # no accepted agent->participant request before the share was delivered.
        teammate_asked_seq: dict[int, int] = {}
        for req in my_requests:
            if req["requester"] in ("JT", "PM") and req.get("crate") is not None:
                seq = int(req["accepted_seq"])
                crate = int(req["crate"])
                if crate not in teammate_asked_seq or seq < teammate_asked_seq[crate]:
                    teammate_asked_seq[crate] = seq
        n_proactive = 0
        for share in my_shares:
            share_crate = record_crate.get((sid, share["record_id"]))
            asked = teammate_asked_seq.get(share_crate) if share_crate is not None else None
            if asked is None or int(share["delivered_seq"]) < asked:
                n_proactive += 1
        n_replies = sum(
            1
            for m in my_messages
            if m["actor"] in ("JT", "PM") and m["status"] == "delivered" and m["request_id"]
        )
        req_ms = first_active_ms(sid, ("teammate_request",))
        action_ms = first_active_ms(sid, ("teammate_request", "record_share"))
        checkin_seq = checkin.get("committed_seq") if checkin else None

        def rel_count(
            kinds: tuple[str, ...],
            before: bool,
            *,
            _seq: Any = checkin_seq,
            _receipts: list[dict[str, Any]] = my_receipts,
            _sid: str = sid,
        ) -> int | None:
            if _seq is None:
                return None
            n = 0
            for rec in _receipts:
                if rec["kind"] not in kinds:
                    continue
                ev = events_by_command.get((_sid, str(rec["command_id"])))
                if ev is None:
                    continue
                if (int(ev["seq"]) < int(_seq)) == before:
                    n += 1
            return n

        result = results.get(sid)
        peer = {
            i["item_id"]: i.get("response")
            for i in my_items
            if str(i["instrument_id"]).startswith("peer_ratings")
        }
        # 2026-10-12 case-file telemetry (remediated 2026-10-13, audit F06-F14):
        # committed telemetry_* events → windowed attention, item timing and
        # file measures. Per-family ``*_state`` fields stay missing_data where
        # instrumentation/observation is absent (never zeroed).
        file_tel = file_telemetry_measures(
            my_events,
            file_size=len([f for f in added if str(f["session_id"]) == sid]) or None,
            added_from_team=my_added,
            participant_shares=my_shares,
            teammate_shares=[
                r for r in shares if str(r["session_id"]) == sid and r["sharer"] != "participant"
            ],
            event_active_ms=lambda seq, _sid=sid: (
                (events_by_seq.get((_sid, int(seq))) or {}).get("active_ms")
                if seq is not None
                else None
            ),
        )
        timing = item_timing(my_events)
        overlap, n_crates, intel_budget = task_shape(sess)
        out.append(
            {
                "research_pid": pids.get(str(sess["participant_id"])),
                "session_id": sess["session_id"],
                "study_key": sess["study_key"],
                "batch_id": sess["batch_id"],
                "batch_group_id": sess.get("batch_group_id"),
                "mode": sess["mode"],
                "timing_mode": sess["timing_mode"],
                "inv_condition": sess["arm"],
                **{
                    key: value
                    for key, value in fidelity[sid].items()
                    if key.startswith("teammate_")
                },
                "arm_provenance": "configured",
                "stage": sess["stage"],
                "terminal_reason": sess["terminal_reason"],
                "revision": sess["revision"],
                "session_created_at": sess["created_at"],
                "manifest_id": sess["manifest_id"],
                # Crate-condition factors (audit E01): the administered task
                # shape — snapshot world_params → manifest task → legacy.
                "crate_overlap": overlap,
                "n_crates": n_crates,
                "intel_budget": intel_budget,
                "manifest_sha256": sess["manifest_sha256"],
                "bundle_sha256": sess["bundle_sha256"],
                "binding_sha256": sess["binding_sha256"],
                "build_source_sha256": sess["build_source_sha256"],
                "behavior_source_sha256": sess.get("behavior_source_sha256"),
                "compatibility_key": sess["compatibility_key"],
                "inv_team_requests": sum(1 for r in my_receipts if r["kind"] == "teammate_request"),
                "inv_shares": len(my_shares),
                "inv_proactive_shares": n_proactive,
                "inv_replies": n_replies,
                "inv_prompts_used": sum(1 for r in my_receipts if r["kind"] == "assistant_request"),
                "inv_entries_added": len(my_added),
                "inv_events": len(my_events),
                # INV-1107: the real first-request latency is request-only; the legacy
                # INV_FINISH `inv_first_request_sec` was counters.first — the earlier of a
                # request OR a share — and is exported separately, clearly named.
                "first_teammate_request_ms": req_ms,
                "legacy_first_action_sec": (action_ms / 1000 if action_ms is not None else None),
                "first_action_provenance": "observed",
                "checkin_committed_seq": checkin_seq,
                "checkin_provenance": "observed",
                "checkin_scheduled_active_ms": (checkin.get("due_active_ms") if checkin else None),
                "inv_own_pressure": checkin.get("own_rating") if checkin else None,
                "inv_pressure_JT": (
                    (checkin.get("assigned_values") or {}).get("JT") if checkin else None
                ),
                "inv_pressure_PM": (
                    (checkin.get("assigned_values") or {}).get("PM") if checkin else None
                ),
                "inv_requests_pre_checkin": rel_count(("teammate_request",), True),
                "inv_requests_post_checkin": rel_count(("teammate_request",), False),
                "inv_prompts_pre_checkin": rel_count(("assistant_request",), True),
                "inv_prompts_post_checkin": rel_count(("assistant_request",), False),
                "shares_pre_checkin": rel_count(("record_share",), True),
                "shares_post_checkin": rel_count(("record_share",), False),
                "inv_peer_rating_JT": peer.get("peer_ratings.JT") or peer.get("JT"),
                "inv_peer_rating_PM": peer.get("peer_ratings.PM") or peer.get("PM"),
                "item_responses_recorded": len(my_items),
                "inv_correct_total": result.get("correct_total") if result else None,
                "inv_correct_teamdep": result.get("correct_teamdep") if result else None,
                "inv_correct_ownfile": result.get("correct_ownfile") if result else None,
                "result_status": result.get("status") if result else None,
                "score_provenance": "observed",
                "item_dwell_ms": item_dwell_ms(my_events),
                # F11: per-item completeness — partial/unmatched intervals are
                # explicit instead of silently dropped.
                "item_timing": timing,
                "item_timing_partial": sorted(k for k, v in timing.items() if v["partial"]),
                **attention_measures(my_events),
                **file_tel,
            }
        )
    return out


def _crate_long(conn: Connection, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per session and assigned crate: truth, final answer, correctness,
    confidence, how the answering evidence was acquired, and world-relative
    latest/stale view flags."""
    session_ids = [r["session_id"] for r in sessions]
    if not session_ids:
        return []
    worlds = {
        str(r["session_id"]): r["content"] for r in _rows(conn, s.world_snapshot, session_ids)
    }
    records = _rows(conn, s.record, session_ids)
    finals = _rows(conn, s.answer_final, session_ids)
    revisions = _rows(conn, s.answer_revision, session_ids)
    confidence = _rows(conn, s.confidence_response, session_ids)
    telemetry_rows = _rows(conn, s.event, session_ids)
    memberships = _rows(conn, s.file_membership, session_ids)
    out: list[dict[str, Any]] = []
    for sess in sessions:
        sid = str(sess["session_id"])
        content = worlds.get(sid) or {}
        crates = {int(c["num"]): c for c in content.get("crates", [])}
        assigned = set(content.get("assigned", []))
        role_of = {int(k): v for k, v in (content.get("role_of") or {}).items()}
        cutoff_of = {int(k): v for k, v in (content.get("cutoff_of") or {}).items()}
        holdings = content.get("holdings") or {}
        ledger = transfer_ledger(conn, sess["session_id"])
        recs = [r for r in records if str(r["session_id"]) == sid]
        member_ids = {f["record_id"] for f in memberships if str(f["session_id"]) == sid}
        file_stats = per_record_file_stats(
            [e for e in telemetry_rows if str(e["session_id"]) == sid]
        )
        crate_records: dict[int, list[dict[str, Any]]] = {}
        for r in recs:
            crate_records.setdefault(int(r["crate"]), []).append(r)
        holder_of = {rid: holder for holder, rids in holdings.items() for rid in rids}
        for crate in sorted(crates):
            info = crates.get(crate, {})
            my_recs = crate_records.get(crate, [])
            stages: dict[str, int] = {}
            for r in my_recs:
                for stage in ledger.get(r["record_id"], []):
                    stages[stage] = stages.get(stage, 0) + 1
            final = next(
                (f for f in finals if str(f["session_id"]) == sid and int(f["crate"]) == crate),
                None,
            )
            conf_rows = [
                c for c in confidence if str(c["session_id"]) == sid and int(c["crate"]) == crate
            ]
            conf = max(conf_rows, key=lambda c: int(c["revision"])) if conf_rows else None
            truth = info.get("truth")
            answer = final.get("location_id") if final else None
            # Per-crate case-file telemetry (2026-10-12; remediated 2026-10-13
            # audit F10/F13). Two distinct "latest" references are exported:
            #   world latest  — max timeline_ordinal over ALL session records
            #                   for the crate (durable; may never have entered
            #                   the participant's file — availability flag);
            #   file latest   — max over file members only.
            # stale_viewed_without_latest is emitted for both reference sets.
            # Policy on missingness: no file telemetry → all file_* NULL;
            # instrumented session but crate has NO file records → NULL
            # (undefined denominator — nothing to view), never 0; crate has
            # file records → measured values (0 allowed).
            file_recs = [r for r in my_recs if r["record_id"] in member_ids]
            viewed_ids = (
                {rid for rid, st in file_stats.items() if st["views"] > 0}
                if file_stats is not None
                else set()
            )
            latest_world = (
                max(my_recs, key=lambda r: int(r["timeline_ordinal"])) if my_recs else None
            )
            world_in_file = bool(
                latest_world is not None and latest_world["record_id"] in member_ids
            )
            latest = max(file_recs, key=lambda r: int(r["timeline_ordinal"])) if file_recs else None
            if file_stats is None or not file_recs:
                n_records_viewed = None
                latest_viewed = None
                stale_without_latest = None
                dwell_crate = None
                latest_world_viewed = None
                stale_without_world_latest = None
            else:
                latest_viewed = latest["record_id"] in viewed_ids if latest else None
                n_records_viewed = sum(1 for r in file_recs if r["record_id"] in viewed_ids)
                stale_without_latest = (
                    (not latest_viewed)
                    and any(
                        r["record_id"] in viewed_ids
                        and int(r["timeline_ordinal"]) < int(latest["timeline_ordinal"])
                        for r in file_recs
                    )
                    if latest
                    else None
                )
                dwell_crate = sum(
                    (file_stats.get(r["record_id"]) or {}).get("dwell_ms", 0) for r in file_recs
                )
                # World-relative: the crate's newest record either never reached
                # the file (availability failure) or was never viewed.
                latest_world_viewed = (
                    (latest_world["record_id"] in viewed_ids)
                    if world_in_file and latest_world is not None
                    else False
                )
                stale_without_world_latest = (not latest_world_viewed) and any(
                    r["record_id"] in viewed_ids
                    and latest_world is not None
                    and int(r["timeline_ordinal"]) < int(latest_world["timeline_ordinal"])
                    for r in file_recs
                )
            out.append(
                {
                    "session_id": sess["session_id"],
                    "arm": sess["arm"],
                    "compatibility_key": sess["compatibility_key"],
                    "crate": crate,
                    "assigned": crate in assigned,
                    "role": role_of.get(crate),  # own/team; 'other' = unassigned (world vocab)
                    "role_provenance": "simulated",
                    "initial_cutoff": cutoff_of.get(crate),
                    "truth": truth,
                    "truth_provenance": "simulated" if truth else None,
                    "n_records": len(my_recs),
                    "records_held_by": sorted(
                        {str(holder_of.get(r["record_id"], "")) for r in my_recs}
                    ),
                    "n_delivered": stages.get("delivered", 0),
                    "n_rendered": stages.get("rendered", 0),
                    "n_in_file": stages.get("in_file", 0),
                    "n_in_context": stages.get("in_context", 0),
                    "n_referenced": stages.get("referenced", 0),
                    "n_never_arrived": stages.get("never", 0),
                    "file_n_records_viewed": n_records_viewed,
                    # In-file reference set (F13): latest *file member* viewed,
                    # and stale-without-latest among file records only.
                    "file_latest_record_viewed": latest_viewed,
                    "file_stale_viewed_without_latest": stale_without_latest,
                    # World-relative reference set: the crate's newest record
                    # overall — availability (did it reach the file at all) and
                    # world-relative stale pattern are separate facts.
                    "latest_world_record_id": (latest_world["record_id"] if latest_world else None),
                    "file_latest_world_in_file": (
                        world_in_file if latest_world is not None else None
                    ),
                    "file_latest_world_viewed": latest_world_viewed,
                    "file_stale_viewed_without_world_latest": stale_without_world_latest,
                    "file_dwell_ms": dwell_crate,
                    "final_answer": answer,
                    "final_answer_revision": final.get("revision") if final else None,
                    "n_answer_revisions": sum(
                        1
                        for r in revisions
                        if str(r["session_id"]) == sid and int(r["crate"]) == crate
                    ),
                    "correct": ((answer == truth) if (answer is not None and truth) else None),
                    "confidence_value": conf.get("value") if conf else None,
                    "confidence_status": conf.get("status") if conf else None,
                    "confidence_provenance": "observed" if conf else None,
                }
            )
    return out


def _model_audit(
    model_calls: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Spec §9.2 model audit join — raw prompt/response text deliberately excluded."""
    snap = {(str(r["session_id"]), str(r["snapshot_id"])): r for r in snapshots}
    by_call: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for o in outputs:
        if o.get("call_id") is not None:
            by_call.setdefault((str(o["session_id"]), str(o["call_id"])), []).append(o)
    out: list[dict[str, Any]] = []
    for call in model_calls:
        key = (str(call["session_id"]), str(call["call_id"]))
        context = snap.get((str(call["session_id"]), str(call["snapshot_id"])), {})
        shown = by_call.get(key, [])
        out.append(
            {
                "session_id": call["session_id"],
                "call_id": call["call_id"],
                "snapshot_id": call["snapshot_id"],
                "purpose": context.get("purpose"),
                "seat": context.get("seat"),
                "attempt": call["attempt"],
                "provider": call["provider"],
                "model": call["model"],
                "model_digest": call["model_digest"],
                "model_provenance": "configured",
                "prompt_sha256": call["prompt_sha256"],
                "status": call["status"],
                "status_provenance": "observed",
                "validation": call["validation"],
                "timeout_ms": call["timeout_ms"],
                "duration_ms": call["duration_ms"],
                "requested_at": call["requested_at"],
                "finished_at": call["finished_at"],
                "requested_active_ms": call["requested_active_ms"],
                "fallback": shown[0]["fallback"] if shown else None,
                "shown_sha256": shown[0]["shown_sha256"] if shown else None,
                "delivered_seq": shown[0]["delivered_seq"] if shown else None,
                "validator_version": shown[0].get("validator_version") if shown else None,
                "display_provenance": "observed" if shown else None,
            }
        )
    return out


def _evidence_transfer(conn: Connection, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per session and record: acquisition path, case-file exposure (view and
    dwell; NULL when the session predates telemetry — missing, never false),
    latest-record flags against in-file and world references, and return latency
    for teammate-added intel."""
    session_ids = [r["session_id"] for r in sessions]
    records = _rows(conn, s.record, session_ids)
    telemetry_rows = _rows(conn, s.event, session_ids)
    memberships = _rows(conn, s.file_membership, session_ids)
    shares = _rows(conn, s.share, session_ids)
    crate_of = {(str(r["session_id"]), r["record_id"]): r["crate"] for r in records}
    out: list[dict[str, Any]] = []
    for sess in sessions:
        sid = str(sess["session_id"])
        # 2026-10-12: per-record case-file exposure (view/dwell). NULL when the
        # session predates the telemetry build — missing, never false.
        # 2026-10-13 (audit F13/F14): latest-record flags against both the
        # in-file and world reference sets, plus per-record return latency for
        # teammate-added intel.
        sess_events = [e for e in telemetry_rows if str(e["session_id"]) == sid]
        sess_records = [r for r in records if str(r["session_id"]) == sid]
        sess_teammate_shares = [
            r for r in shares if str(r["session_id"]) == sid and r["sharer"] != "participant"
        ]
        member_of = {
            str(f["record_id"]): f
            for f in memberships
            if str(f["session_id"]) == sid and f["source"] == "added_from_team"
        }
        file_stats = per_record_file_stats(sess_events)
        # Latest flags per crate: within-file and world reference sets (F13).
        latest_in_file: set[str] = set()
        latest_world: set[str] = set()
        by_crate: dict[int, list[dict[str, Any]]] = {}
        for r in sess_records:
            by_crate.setdefault(int(r["crate"]), []).append(r)
        member_ids = set(member_of) | {
            str(f["record_id"]) for f in memberships if str(f["session_id"]) == sid
        }
        for crate_recs in by_crate.values():
            world_rec = max(crate_recs, key=lambda r: int(r["timeline_ordinal"]))
            latest_world.add(str(world_rec["record_id"]))
            file_recs = [r for r in crate_recs if r["record_id"] in member_ids]
            if file_recs:
                file_rec = max(file_recs, key=lambda r: int(r["timeline_ordinal"]))
                latest_in_file.add(str(file_rec["record_id"]))
        for record_id, stages in sorted(
            transfer_summary(conn, sess["session_id"])["ledger"].items()
        ):
            stat = (file_stats or {}).get(record_id)
            member = member_of.get(record_id)
            latency = (
                record_return_latency(sess_events, member, sess_teammate_shares)
                if member is not None and file_stats is not None
                else None
            )
            row: dict[str, Any] = {
                "session_id": sess["session_id"],
                "record_id": record_id,
                "crate": crate_of.get((sid, record_id)),
                "compatibility_key": sess["compatibility_key"],
                "stages": list(stages),
                "viewed_in_file": (
                    None if file_stats is None else (stat or {}).get("views", 0) > 0
                ),
                "file_dwell_ms": (None if file_stats is None else (stat or {}).get("dwell_ms", 0)),
                "is_latest_in_file": record_id in latest_in_file,
                "is_latest_world": record_id in latest_world,
                # F14: occurrence-time return latency (same-epoch client_ms) —
                # None when either anchor is missing or epochs differ;
                # server_ms is the persist-clock bound, kept labeled.
                "file_return_occ_ms": (latency or {}).get("occ_ms"),
                "file_return_server_ms": (latency or {}).get("server_ms"),
            }
            for stage in (
                "held",
                "eligible",
                "selected",
                "delivered",
                "rendered",
                "in_file",
                "in_context",
                "referenced",
                "in_answer",
                "never",
            ):
                row[f"reached_{stage}"] = stage in stages
            out.append(row)
    return out


def _stamp_feature_flags(export: StudyExport) -> None:
    """Append ``flag_<name>`` columns to every row of every table and add
    ``flags_at_entry`` provenance on the per-session tables.

    ``flags_at_entry`` is read off the session's own ``session_opened`` event
    payload (recorded at entry since 2026-10-15); sessions that predate the
    recording carry ``None`` — a real signal, not imputation."""
    flags = export.feature_flags
    if not flags:
        return
    stamped = flag_columns(flags)
    for table in export.tables.values():
        for row in table.rows:
            row.update(stamped)
    entry_flags: dict[str, dict[str, bool]] = {}
    for ev in export.tables.get("event", ExportTable("event", [], "")).rows:
        if ev.get("type") == "session_opened" and isinstance(ev.get("payload"), dict):
            recorded = ev["payload"].get("feature_flags")
            if isinstance(recorded, dict) and recorded:
                entry_flags[str(ev["session_id"])] = dict(recorded)
    for name in ("session", "participant_wide"):
        for row in export.tables.get(name, ExportTable(name, [], "")).rows:
            row["flags_at_entry"] = entry_flags.get(str(row["session_id"]))


def export_study(
    engine: Engine,
    *,
    batch_id: str | None = None,
    study_key: str | None = None,
    session_ids: list[uuid.UUID] | None = None,
    coding_run_id: uuid.UUID | None = None,
    coding_override: Any = None,
    feature_flags: Mapping[str, bool] | None = None,
) -> StudyExport:
    """Whole-study export under one REPEATABLE READ snapshot (INV-1106).

    Caller supplies the selection filter; the export is offline and identity-free. At least
    one filter should be supplied — an unfiltered ``export_study`` covers every committed
    session in the database (intentional for full-study dumps; the manifest records it).

    ``coding_run_id`` additionally attaches the ``semantic_coding`` product for that
    run (INV-1205/1208). It passes the analysis guard — mixed or incomplete runs raise
    ``SemanticGuardError`` unless ``coding_override`` (a ``GuardOverride``) is supplied;
    exact-record tables are unaffected either way.
    """
    from app.coding.guard import GuardOverride
    from app.export.semantic import semantic_coding_table

    filters: dict[str, Any] = {}
    if batch_id is not None:
        filters["batch_id"] = batch_id
    if study_key is not None:
        filters["study_key"] = study_key
    if session_ids is not None:
        filters["session_ids"] = [str(i) for i in session_ids]
    if coding_run_id is not None:
        filters["coding_run_id"] = str(coding_run_id)
    if coding_override is not None and not isinstance(coding_override, GuardOverride):
        raise TypeError("coding_override must be a coding.guard.GuardOverride")

    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="REPEATABLE READ")
        snapshot_time: datetime = conn.execute(text("SELECT statement_timestamp()")).scalar_one()
        session_rows = _sessions(
            conn, {"batch_id": batch_id, "study_key": study_key, "session_ids": session_ids}
        )
        sids = [r["session_id"] for r in session_rows]

        export = StudyExport(
            kind="research_study",
            snapshot_time=snapshot_time,
            filters=filters,
        )
        export.add(
            ExportTable(
                "session",
                session_rows,
                "Session registry rows (version stamps, arm, stage, compatibility_key)",
            )
        )
        for table_name, export_name in PASSTHROUGH.items():
            export.add(
                ExportTable(
                    export_name,
                    (
                        _item_response_rows(conn, sids, feature_flags=feature_flags)
                        if export_name == "item_response"
                        else _rows(conn, s.metadata.tables[f"{s.APP}.{table_name}"], sids)
                    ),
                    f"app.{table_name} committed rows for the selected sessions",
                )
            )
        manifest_ids = sorted({r["manifest_id"] for r in session_rows})
        batch_ids = sorted({r["batch_id"] for r in session_rows})
        export.add(
            ExportTable(
                "experiment_manifest",
                [
                    dict(r)
                    for r in conn.execute(
                        select(s.experiment_manifest).where(
                            s.experiment_manifest.c.manifest_id.in_(manifest_ids)
                        )
                    )
                    .mappings()
                    .all()
                ]
                if manifest_ids
                else [],
                "Registered manifests bound by the selected sessions",
            )
        )
        export.add(
            ExportTable(
                "batch",
                [
                    dict(r)
                    for r in conn.execute(select(s.batch).where(s.batch.c.batch_id.in_(batch_ids)))
                    .mappings()
                    .all()
                ]
                if batch_ids
                else [],
                "Enrollment batches the selected sessions belong to",
            )
        )
        export.add(
            ExportTable(
                "participant_wide",
                _participant_wide(conn, session_rows),
                "One row per session: study/arm/versions/status + behavioral summaries",
                metric_version=METRIC_VERSION,
            )
        )
        export.add(
            ExportTable(
                "crate_long",
                _crate_long(conn, session_rows),
                "One row per session x crate: role/cutoff/truth/answer/confidence/acquisition",
                metric_version=METRIC_VERSION,
            )
        )
        export.add(
            ExportTable(
                "model_audit",
                _model_audit(
                    export.tables["model_call"].rows,
                    export.tables["displayed_output"].rows,
                    export.tables["context_snapshot"].rows,
                ),
                "model_call x displayed_output x context join (no raw text)",
                metric_version=METRIC_VERSION,
            )
        )
        export.add(
            ExportTable(
                "evidence_transfer",
                _evidence_transfer(conn, session_rows),
                "Per-record transfer-ledger stage reachability",
                metric_version=METRIC_VERSION,
            )
        )
        export.add(
            ExportTable(
                "fidelity_rate",
                fidelity_rates(
                    session_rows,
                    export.tables["model_call"].rows,
                    export.tables["message"].rows,
                    export.tables["scheduled_job"].rows,
                    export.tables["context_snapshot"].rows,
                    filters=filters,
                ),
                "empty/fallback/late/suppressed/failed/off-schedule rates "
                "by compatibility_key x arm x source x model x policy",
                metric_version=METRIC_VERSION,
            )
        )
        export.add(
            ExportTable(
                "incident_summary",
                incident_summary(
                    session_rows,
                    export.tables["incident_interval"].rows,
                    filters=filters,
                ),
                "Descriptive technical incidents by compatibility_key x clock_effect x kind",
                metric_version=METRIC_VERSION,
            )
        )
        if coding_run_id is not None:
            table, _report = semantic_coding_table(
                conn, coding_run_id, session_ids=sids, override=coding_override
            )
            export.add(table)
    export.feature_flags = dict(feature_flags or {})
    _stamp_feature_flags(export)
    return export


_PASSTHROUGH_BY_NAME = {export_name: app_name for app_name, export_name in PASSTHROUGH.items()}

# Every name in the research-export vocabulary. All are safe for the researcher role:
# payment-domain tables never enter this map (the module-level assertion below), and
# ``semantic_coding`` is excluded because it requires a coding run + guard decision.
EXPORT_TABLE_NAMES: frozenset[str] = frozenset(
    set(_PASSTHROUGH_BY_NAME)
    | {
        "session",
        "experiment_manifest",
        "batch",
        "participant_wide",
        "crate_long",
        "model_audit",
        "evidence_transfer",
        "fidelity_rate",
        "incident_summary",
        # Synthesized verbatim-text table (2026-10-12 owner direction): chat
        # message bodies + model call prompt/response + event payloads in one
        # chronological file. Not a passthrough — built by _chat_text_rows.
        "chat_text",
    }
)

# Browser-download restrictions (dashboard audit DC-01, plan §3.2 review).
# Free text is emitted verbatim in every downloadable file — owner direction
# 2026-10-12 (item responses), 2026-10-15 (chat + model calls) and 2026-10-16
# (everything): no personal identifiers are stored and the PI reviews
# open-ended responses in these files. Two restrictions remain:
#
# - ``DOWNLOAD_FREE_TEXT_TABLES`` may carry *participant* free text anywhere in
#   the row (quoted chat inside prompts, typed command results, event payloads):
#   a session whose latest data-use choice is missing or not ``use_all`` is
#   dropped from these tables entirely. It remains on exact-record tables.
#   ``item_response`` applies the same drop at row scope
#   (``_ROW_SCOPED_TEXT_TABLES``): only its open_ended rows carry prose.
# - ``DOWNLOAD_HIDDEN_COLUMNS`` strips columns that enable world reconstruction
#   (the seed is an ops/audit field, not an analysis field).

# Tables whose participant prose lives in only a subset of rows — keyed by
# export-table name; the row predicate lives in ``download_table``.
_ROW_SCOPED_TEXT_TABLES = frozenset({"item_response"})

DOWNLOAD_FREE_TEXT_TABLES: frozenset[str] = frozenset(
    {
        "message",
        "model_call",
        "model_audit",
        "displayed_output",
        "event",
        "outbox",
        "command_receipt",
        "scheduled_job",
        "context_snapshot",
        "world_snapshot",
        "agent_state",
        "claim",
        # verbatim-text table — carries participant free text, so the
        # non-``use_all`` session drop must still apply.
        "chat_text",
        # AI_QA_ENABLED: the rules-Q&A exchange rows are participant free
        # text (question + answer) — same consent-driven whole-table drop.
        "qa_exchange",
    }
)

DOWNLOAD_HIDDEN_COLUMNS: dict[str, tuple[str, ...]] = {
    "session": ("seed_hex",),
}


def _is_open_ended_row(row: Mapping[str, Any]) -> bool:
    """True for ``item_response`` rows of the open_ended instrument — the
    legacy code-defined id (v10-era rows) or the BA-105 bound artifact id.
    These are the only rows whose ``response.value`` is participant prose."""
    from app.services.lifecycle import OPEN_ENDED_INSTRUMENT_IDS

    return row.get("instrument_id") in OPEN_ENDED_INSTRUMENT_IDS


# The open_ended follow-up pair — artifact ``open_ended.tp_followup`` and the
# legacy code-defined instrument share the item ids — also exports both prose
# answers as named per-session columns so each open_ended row is
# self-describing without parsing ``response.value`` (2026-10-15 owner
# request): ``crate_strategy`` → ``response1`` ("how did you decide on a
# location for each of your crates?"), ``source_reliance`` → ``response2``
# ("which source to rely on"). Stamped on open_ended rows only — a non-use_all
# session loses those rows whole (``_ROW_SCOPED_TEXT_TABLES``), so scoping the
# columns to them keeps declined prose from leaking onto surviving rows.
OPEN_ENDED_RESPONSE_COLUMNS = {
    "crate_strategy": "response1",
    "source_reliance": "response2",
}


@functools.lru_cache(maxsize=1)
def _instrument_item_texts() -> dict[tuple[str, int, str], str]:
    """``(instrument_id, instrument_version, item_id)`` → the item wording as
    published — every version of every instrument artifact in the content
    store, plus the frozen legacy ``open_ended_followup`` items. Empty when the
    store is unavailable: exports then carry ``question`` as null rather than
    failing the whole run on a missing content dir."""
    texts: dict[tuple[str, int, str], str] = {}
    try:
        from app.domain.manifests.store import ContentError, ContentStore

        store = ContentStore.load_default()
    except (ContentError, OSError):
        # missing/invalid content dir — wording column degrades to null
        store = None
    if store is not None:
        from app.domain.manifests.artifacts import InstrumentArtifact

        for stored in store.artifacts.values():
            artifact = stored.artifact
            if not isinstance(artifact, InstrumentArtifact):
                continue
            for item in artifact.body.items:
                texts[(artifact.artifact_id, artifact.version, item.item_id)] = item.text
    from app.services.lifecycle import (
        LEGACY_OPEN_ENDED_ITEMS,
        LEGACY_OPEN_ENDED_VERSION,
    )

    for legacy_item in LEGACY_OPEN_ENDED_ITEMS:
        texts[("open_ended_followup", LEGACY_OPEN_ENDED_VERSION, legacy_item["item_id"])] = (
            legacy_item["text"]
        )
    return texts


def _item_response_rows(
    conn: Connection,
    session_ids: list[uuid.UUID],
    *,
    feature_flags: Mapping[str, bool] | None = None,
) -> list[dict[str, Any]]:
    """``app.item_response`` rows with ``question`` + ``response1``/``response2``
    added — ``question`` is the item wording exactly as asked (flag-projected:
    the ``source_reliance`` assistant clause follows the session's recorded
    ``flags_at_entry`` when present, else the deployment posture); each
    open_ended follow-up row carries *both* of the session's verbatim answers
    (``crate_strategy`` in ``response1``, ``source_reliance`` in ``response2``);
    all other rows carry the split columns as ``None``."""
    from app.services.lifecycle import instrument_item_text

    rows = _rows(conn, s.item_response, session_ids)
    texts = _instrument_item_texts()
    entry_flags = _entry_flags(conn, session_ids)
    deployed = feature_flags or {}
    answers: dict[str, dict[str, Any]] = {}
    for row in rows:
        if _is_open_ended_row(row) and isinstance(row.get("response"), dict):
            item_id = row.get("item_id")
            if isinstance(item_id, str):
                answers.setdefault(str(row["session_id"]), {})[item_id] = row["response"].get(
                    "value"
                )
    for row in rows:
        row["response1"] = row["response2"] = None
        if _is_open_ended_row(row):
            by_item = answers.get(str(row["session_id"]), {})
            row["response1"] = by_item.get("crate_strategy")
            row["response2"] = by_item.get("source_reliance")
        question: Any = None
        instrument_id = row.get("instrument_id")
        item_id = row.get("item_id")
        version = row.get("instrument_version")
        if isinstance(instrument_id, str) and isinstance(item_id, str) and version is not None:
            try:
                question = texts.get((instrument_id, int(version), item_id))
            except (TypeError, ValueError):
                question = None
        if isinstance(question, str) and isinstance(item_id, str):
            at_entry = entry_flags.get(str(row["session_id"]), {})
            ai = bool(
                at_entry.get("ai_assistant_enabled", deployed.get("ai_assistant_enabled", True))
            )
            question = instrument_item_text(item_id, question, ai_assistant=ai)
        row["question"] = question
    return rows


def _entry_flags(conn: Connection, session_ids: list[uuid.UUID]) -> dict[str, dict[str, bool]]:
    """``session_id`` → the ``feature_flags`` recorded in its ``session_opened``
    event payload (since 2026-10-15). Sessions that predate the recording or
    opened under a flagless build are absent — a real signal, not imputation."""
    if not session_ids:
        return {}
    out: dict[str, dict[str, bool]] = {}
    rows = conn.execute(
        select(s.event.c.session_id, s.event.c.payload).where(
            s.event.c.type == "session_opened",
            s.event.c.session_id.in_(session_ids),
        )
    ).all()
    for sid, payload in rows:
        recorded = payload.get("feature_flags") if isinstance(payload, dict) else None
        if isinstance(recorded, dict) and recorded:
            out[str(sid)] = dict(recorded)
    return out


def download_table(
    conn: Connection,
    name: str,
    session_rows: list[dict[str, Any]],
    filters: dict[str, Any],
    *,
    data_use: Mapping[uuid.UUID, str] | None = None,
    feature_flags: Mapping[str, bool] | None = None,
) -> list[dict[str, Any]]:
    """``single_table`` rows after the browser-download restrictions: hidden
    columns stripped and non-``use_all`` open_ended rows dropped. The byte-level
    table definition is unchanged and free text is verbatim.

    ``data_use`` is the latest-data-use map (``session_id`` → choice). It is
    required for row-scoped text tables: ``item_response`` open_ended rows are
    participant prose, so a session without an explicit ``use_all`` choice loses
    them entirely — the DC-01 whole-table drop applied at row scope, because the
    same table's exact-record items are not free text."""
    table = single_table(conn, name, session_rows, filters, feature_flags=feature_flags)
    rows = [dict(r) for r in table.rows]
    for col in DOWNLOAD_HIDDEN_COLUMNS.get(name, ()):
        for row in rows:
            row.pop(col, None)
    if name in _ROW_SCOPED_TEXT_TABLES:
        du = data_use or {}
        kept: list[dict[str, Any]] = []
        for r in rows:
            sid = r.get("session_id")
            use_all = isinstance(sid, uuid.UUID) and du.get(sid) == USE_ALL
            if not _is_open_ended_row(r) or use_all:
                kept.append(r)
        rows = kept
    # Parity with the published export: flag_<name> on every row, plus
    # flags_at_entry provenance on the per-session tables.
    stamped = flag_columns(feature_flags or {})
    if stamped:
        for row in rows:
            row.update(stamped)
        if name in ("session", "participant_wide"):
            entry_flags = _entry_flags(
                conn, [r["session_id"] for r in rows if isinstance(r.get("session_id"), uuid.UUID)]
            )
            for row in rows:
                row["flags_at_entry"] = entry_flags.get(str(row["session_id"]))
    return rows


def session_rows_for_ids(conn: Connection, session_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """Session registry rows for an already-resolved cohort (export input)."""
    return _sessions(conn, {"session_ids": session_ids})


def _chat_text_rows(conn: Connection, sids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """One row per free-text artifact across the scoped sessions — chat
    message bodies, model-call prompts/responses, and event payloads — in
    session-then-time order. Verbatim by owner direction (2026-10-12): no
    redaction withholding; sessions without ``use_all`` are dropped upstream
    via ``DOWNLOAD_FREE_TEXT_TABLES``.
    """
    rows: list[dict[str, Any]] = []
    for r in (
        conn.execute(
            select(s.message)
            .where(s.message.c.session_id.in_(sids))
            .order_by(s.message.c.session_id, s.message.c.committed_seq)
        )
        .mappings()
        .all()
    ):
        rows.append(
            {
                "session_id": r["session_id"],
                "occurred_at": r["created_at"],
                "active_ms": r["scheduled_for_active_ms"],
                "source": "message",
                "actor": r["actor"],
                "kind": r["status"],
                "channel": r["channel"],
                "ref_id": r["message_id"],
                "text": r["body"],
            }
        )
    for r in (
        conn.execute(
            select(s.model_call)
            .where(s.model_call.c.session_id.in_(sids))
            .order_by(s.model_call.c.session_id, s.model_call.c.requested_at)
        )
        .mappings()
        .all()
    ):
        for part, txt in (("prompt", r["input"]), ("response", r["raw_response"])):
            rows.append(
                {
                    "session_id": r["session_id"],
                    "occurred_at": r["requested_at"],
                    "active_ms": r["requested_active_ms"],
                    "source": f"model_call_{part}",
                    "actor": "model",
                    "kind": r["status"],
                    "channel": None,
                    "ref_id": r["call_id"],
                    "text": txt,
                }
            )
    for r in (
        conn.execute(
            select(s.event)
            .where(s.event.c.session_id.in_(sids))
            .order_by(s.event.c.session_id, s.event.c.seq)
        )
        .mappings()
        .all()
    ):
        rows.append(
            {
                "session_id": r["session_id"],
                "occurred_at": r["server_time"],
                "active_ms": r["active_ms"],
                "source": "event",
                "actor": r["actor"],
                "kind": r["type"],
                "channel": None,
                "ref_id": r["event_id"],
                "text": None if r["payload"] is None else json.dumps(r["payload"], default=str),
            }
        )
    rows.sort(key=lambda r: (str(r["session_id"]), str(r["occurred_at"] or "")))
    return rows


def single_table(
    conn: Connection,
    name: str,
    session_rows: list[dict[str, Any]],
    filters: dict[str, Any],
    *,
    feature_flags: Mapping[str, bool] | None = None,
) -> ExportTable:
    """One table with the identical definition ``export_study`` writes — the
    per-table admin download path must not drift from the published export."""
    sids = [r["session_id"] for r in session_rows]

    def pt(export_name: str) -> list[dict[str, Any]]:
        if export_name == "item_response":
            return _item_response_rows(conn, sids, feature_flags=feature_flags)
        return _rows(conn, s.metadata.tables[f"{s.APP}.{_PASSTHROUGH_BY_NAME[export_name]}"], sids)

    if name in _PASSTHROUGH_BY_NAME:
        return ExportTable(
            name,
            pt(name),
            f"app.{_PASSTHROUGH_BY_NAME[name]} committed rows for the selected sessions",
        )
    if name == "session":
        return ExportTable(
            name,
            session_rows,
            "Session registry rows (version stamps, arm, stage, compatibility_key)",
        )
    if name == "experiment_manifest":
        manifest_ids = sorted({r["manifest_id"] for r in session_rows})
        return ExportTable(
            name,
            [
                dict(r)
                for r in conn.execute(
                    select(s.experiment_manifest).where(
                        s.experiment_manifest.c.manifest_id.in_(manifest_ids)
                    )
                )
                .mappings()
                .all()
            ]
            if manifest_ids
            else [],
            "Registered manifests bound by the selected sessions",
        )
    if name == "batch":
        batch_ids = sorted({r["batch_id"] for r in session_rows})
        return ExportTable(
            name,
            [
                dict(r)
                for r in conn.execute(select(s.batch).where(s.batch.c.batch_id.in_(batch_ids)))
                .mappings()
                .all()
            ]
            if batch_ids
            else [],
            "Enrollment batches the selected sessions belong to",
        )
    if name == "chat_text":
        return ExportTable(
            name,
            _chat_text_rows(conn, sids),
            "All session free text verbatim: chat message bodies, model-call "
            "prompts/responses, committed event payloads (owner direction "
            "2026-10-12 — no redaction withholding; use-all sessions only)",
        )
    if name == "participant_wide":
        return ExportTable(
            name,
            _participant_wide(conn, session_rows),
            "One row per session: study/arm/versions/status + behavioral summaries",
            metric_version=METRIC_VERSION,
        )
    if name == "crate_long":
        return ExportTable(
            name,
            _crate_long(conn, session_rows),
            "One row per session x crate: role/cutoff/truth/answer/confidence/acquisition",
            metric_version=METRIC_VERSION,
        )
    if name == "model_audit":
        return ExportTable(
            name,
            _model_audit(
                pt("model_call"),
                pt("displayed_output"),
                pt("context_snapshot"),
            ),
            "model_call x displayed_output x context join (no raw text)",
            metric_version=METRIC_VERSION,
        )
    if name == "evidence_transfer":
        return ExportTable(
            name,
            _evidence_transfer(conn, session_rows),
            "Per-record transfer-ledger stage reachability",
            metric_version=METRIC_VERSION,
        )
    if name == "fidelity_rate":
        return ExportTable(
            name,
            fidelity_rates(
                session_rows,
                pt("model_call"),
                pt("message"),
                pt("scheduled_job"),
                pt("context_snapshot"),
                filters=filters,
            ),
            "empty/fallback/late/suppressed/failed/off-schedule rates "
            "by compatibility_key x arm x source x model x policy",
            metric_version=METRIC_VERSION,
        )
    if name == "incident_summary":
        return ExportTable(
            name,
            incident_summary(session_rows, pt("incident_interval"), filters=filters),
            "Descriptive technical incidents by compatibility_key x clock_effect x kind",
            metric_version=METRIC_VERSION,
        )
    raise KeyError(f"unknown export table {name!r}")


def table_columns(name: str, rows: list[dict[str, Any]]) -> list[str]:
    """Column order for an export table — ``columns_of`` plus table-local
    placement rules. Shared by ``write_export`` and the per-table download so
    both emit byte-identical headers."""
    columns = columns_of(rows)
    # Researchers read ``response`` values against the item wording — keep
    # ``question`` immediately after it instead of alphabetically sorted.
    if name == "item_response" and "question" in columns and "response" in columns:
        columns.remove("question")
        columns.insert(columns.index("response") + 1, "question")
    return columns


def write_export(export: StudyExport, out_dir: Path) -> dict[str, Any]:
    """Write every table as formula-safe UTF-8 CSV plus a ``manifest.json`` pinning
    snapshot time, filters, schema/metric versions, row counts and checksums (spec §9.2)."""
    export_dir(out_dir)
    flag_names = list(flag_columns(export.feature_flags))
    manifest_tables: dict[str, Any] = {}
    for name, table in sorted(export.tables.items()):
        columns = table_columns(name, table.rows)
        # Empty tables still carry the flag header — every file is
        # self-describing even when it has no data rows.
        columns.extend(c for c in flag_names if c not in columns)
        file_sha = write_csv(out_dir / f"{name}.csv", columns, table.rows)
        entry: dict[str, Any] = {
            "file": f"{name}.csv",
            "columns": columns,
            "row_count": len(table.rows),
            "file_sha256": file_sha,
            "rows_sha256": table.checksum,
            "schema_version": table.schema_version,
            "filter": export.filters,
            "description": table.description,
        }
        if table.metric_version:
            entry["metric_version"] = table.metric_version
        manifest_tables[name] = entry
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "requirement": "INV-1106",
        "kind": export.kind,
        "snapshot_time": export.snapshot_time.isoformat(),
        "snapshot_isolation": "REPEATABLE READ",
        "schema_version": EXPORT_SCHEMA_VERSION,
        "metric_version": METRIC_VERSION,
        "filters": export.filters,
        "feature_flags": export.feature_flags,
        "feature_flags_scope": (
            "deployment flag values at export time; every row carries them as "
            "flag_<name> columns. Per-session posture is flags_at_entry on the "
            "session/participant_wide tables (recorded in session_opened; null "
            "for sessions that predate flag recording)."
        ),
        "provenance_labels": PROVENANCE_LABELS,
        "identity_boundary": (
            "identity.* is never read by this export; platform linkage appears only in the "
            "restricted compensation product (app.export.restricted)"
        ),
        "tables": manifest_tables,
    }
    text_out = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(text_out, encoding="utf-8")
    manifest_path.chmod(EXPORT_FILE_MODE)
    manifest["manifest_sha256"] = hashlib.sha256(text_out.encode("utf-8")).hexdigest()
    return manifest


# Guard: a research export must never pick up a payment-domain table by accident — if the
# schema grows a new session-owned restricted table, this check turns drift into a failure.
assert set(SESSION_TABLES) >= _RESTRICTED_SESSION_TABLES, (
    "restricted payment tables must remain session-owned"
)
assert not (_RESTRICTED_SESSION_TABLES & set(PASSTHROUGH)), (
    "payment-domain tables must not appear in a research export"
)
# Drift guard: participant display initials are identity-adjacent — they must never
# become a passthrough research-export table (UX round 2).
assert "participant_display" not in PASSTHROUGH, (
    "participant initials must never appear in a research export"
)
