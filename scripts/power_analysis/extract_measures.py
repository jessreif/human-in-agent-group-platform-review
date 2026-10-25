"""Read-only extract of per-session inputs for the pre-registered DV1/DV2/DV3 + covariates.

Run inside the API container (it needs the ``app`` package and the runtime DSN):

  docker exec -i -w /srv/investigators -e MANIFEST_PREFIX=tp_ai \
      investigators-api python - < scripts/power_analysis/extract_measures.py > raw_sessions.json

Env:
  MANIFEST_PREFIX  manifest family to include (default ``tp_ai``; ``tp_`` = every TP build)
  GROUP_LIKE       SQL LIKE pattern on the fielding round (default ``study.live.%``)

Every live session in matching rounds is emitted (all stages) so the funnel can be
reported; analysis keeps completers only. TEST/QA/E2E/preview participant IDs are dropped.

Window rule (pre-registration): "from when teammates report their time pressure until the
task ends" = event seq > ``checkin_reveal`` and <= ``task_deadline`` (fallback
``answers_frozen``). Pre-window = seq < ``checkin_open``. Ordering by the per-session event
seq avoids reconstructing active time across the paused check-in clock.
"""

import json
import os

from sqlalchemy import text

from app.db.engine import make_engine
from app.services import engines
from app.settings import Settings

TEST_PREFIXES = ("TEST", "QA", "E2E", "PREVIEW", "CONNECT:TEST")
TEST_EXACT = {"connect:ABCDE", "connect:ABC123", "connect:a"}
MANIFEST_PREFIX = os.environ.get("MANIFEST_PREFIX", "tp_ai")
GROUP_LIKE = os.environ.get("GROUP_LIKE", "study.live.%")
ITEM_IDS = [f"tp_post_{i:02d}" for i in range(1, 12)]

e = make_engine(Settings().DATABASE_URL.get_secret_value())
out = []
with e.begin() as c:
    sess = c.execute(
        text(
            "select s.session_id, coalesce(s.batch_group_id, s.batch_id) g, s.batch_id, s.arm,"
            " s.manifest_id, s.stage, s.terminal_reason, s.created_at, p.research_pid pid"
            " from app.session s join app.participant p on p.participant_id=s.participant_id"
            " where s.mode='live' and s.manifest_id like :mp"
            " and coalesce(s.batch_group_id, s.batch_id) like :gl order by s.created_at"
        ),
        {"mp": MANIFEST_PREFIX + "%", "gl": GROUP_LIKE},
    ).all()
    for r in sess:
        pid = r.pid or ""
        if pid.upper().startswith(TEST_PREFIXES) or pid in TEST_EXACT:
            continue
        sid = str(r.session_id)
        # 2026-10-15 crate-condition factors (audit G02): the administered task
        # shape — persisted world_params first (the administered world), the
        # pinned manifest's task second, legacy defaults last. Exported per
        # session so analysis can stratify / reject unsupported pooled specs.
        wp = (
            c.execute(
                text(
                    "select content->'world_params' wp from app.world_snapshot"
                    " where session_id=:s"
                ),
                {"s": sid},
            ).scalar()
            or {}
        )
        if isinstance(wp, str):
            wp = json.loads(wp)
        task = (
            c.execute(
                text("select content->'task' t from app.experiment_manifest where manifest_id=:m"),
                {"m": r.manifest_id},
            ).scalar()
            or {}
        )
        if isinstance(task, str):
            task = json.loads(task)
        n_crates = int(wp.get("n_crates") or task.get("n_crates") or 8)
        d = {
            "sid": sid[:8],
            "session_id": sid,
            "round": r.g,
            "member_batch": r.batch_id,
            "manifest": r.manifest_id,
            "arm": r.arm,
            "crate_overlap": wp.get("crate_overlap") or task.get("crate_overlap") or "independent",
            "n_crates": n_crates,
            "intel_budget": wp.get("participant_intel_budget", task.get("participant_intel_budget")),
            "stage": r.stage,
            "terminal_reason": r.terminal_reason,
            "created_at": r.created_at,
        }
        marks = {
            t: c.execute(
                text("select min(seq) from app.event where session_id=:s and type=:t"),
                {"s": sid, "t": t},
            ).scalar()
            for t in ("checkin_open", "checkin_reveal", "task_deadline", "answers_frozen")
        }
        d["marks"] = marks
        d["end_seq"] = marks["task_deadline"] or marks["answers_frozen"]
        d["own_rating"] = c.execute(
            text(
                "select own_rating from app.manipulation_exposure where session_id=:s"
                " and kind='pressure_checkin'"
            ),
            {"s": sid},
        ).scalar()

        accepted = c.execute(
            text(
                "select event_id, seq, payload->>'body' b,"
                " coalesce(payload->>'channel','team') ch from app.event"
                " where session_id=:s and type='message_accepted' and actor='participant'"
                " order by seq"
            ),
            {"s": sid},
        ).all()
        seq_of = {str(m.event_id): m.seq for m in accepted}
        tmr = c.execute(
            text(
                "select seq, parent_event_id, payload->>'via' via, payload->>'crate' crate"
                " from app.event where session_id=:s and type='teammate_requested' order by seq"
            ),
            {"s": sid},
        ).all()
        logged = {seq_of.get(str(t.parent_event_id)) for t in tmr}
        d["team_msgs"] = []
        for m in accepted:
            if m.ch != "team":
                continue
            body = m.b or ""
            # Crate mentions must parse against the session's own crate range —
            # "crate 12" is real traffic under the no-overlap shape and
            # out-of-range noise under the four-crate one.
            crates = engines.crates_referenced(body, n_crates=n_crates)
            d["team_msgs"].append(
                {
                    "seq": m.seq,
                    "body": body,
                    "crates": sorted(crates),
                    "logged": m.seq in logged,
                    "reparse": bool(
                        engines.wants_intel_ask(body, crates)
                        or (
                            (not crates)
                            and engines.unresolved_intel_ask(body, n_crates=n_crates)
                        )
                    ),
                }
            )
        # Structured (menu) requests have no participant-message parent; typed builds have none.
        d["menu_requests"] = [
            {"seq": t.seq, "via": t.via, "crate": t.crate}
            for t in tmr
            if t.parent_event_id is None or str(t.parent_event_id) not in seq_of
        ]
        d["tm_via"] = sorted({t.via for t in tmr if t.via})
        d["assistant_q"] = [
            {"seq": a.seq, "body": a.b, "crate": a.crate}
            for a in c.execute(
                text(
                    "select seq, payload->>'body' b, payload->>'crate' crate from app.event"
                    " where session_id=:s and type='assistant_requested' order by seq"
                ),
                {"s": sid},
            ).all()
        ]
        res = c.execute(
            text("select counts from app.result where session_id=:s"), {"s": sid}
        ).scalar()
        d["per_crate"] = (
            [
                {
                    "crate": x["crate"],
                    "role": x["role"],
                    "correct": x["correct"] is True,
                    "answered": bool(x.get("final_location")),
                }
                for x in (res if isinstance(res, dict) else json.loads(res))["per_crate"]
            ]
            if res
            else None
        )
        d["items"] = {
            i.item_id: (i.response or {}).get("value")
            for i in c.execute(
                text(
                    "select item_id, response from app.item_response where session_id=:s"
                    " and instrument_id like 'post_task%' and item_id = any(:ids)"
                ),
                {"s": sid, "ids": ITEM_IDS},
            ).all()
        }
        # The latest recorded choice wins (POST-LAUNCH §E).
        d["data_use"] = c.execute(
            text(
                "select choice from app.data_use_choice where session_id=:s"
                " order by recorded_seq desc limit 1"
            ),
            {"s": sid},
        ).scalar()
        # Telemetry rows (2026-10-12 build adds telemetry_file_* + share_attempt;
        # absent for earlier sessions — an empty list means either pre-build or a
        # dead queue, both "no data", never zero. See CODEBOOK cb-1.8).
        d["telemetry"] = [
            {"seq": t.seq, "type": t.type, "active_ms": t.active_ms, "payload": t.payload}
            for t in c.execute(
                text(
                    "select seq, type, active_ms, payload from app.event"
                    " where session_id=:s and type like 'telemetry_%' order by seq"
                ),
                {"s": sid},
            ).all()
        ]
        out.append(d)
print(json.dumps(out, indent=1, default=str))
