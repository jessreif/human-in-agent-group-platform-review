#!/usr/bin/env python3
"""Post-deploy case-file telemetry smoke on study.smoke.v11.

Drives one synthetic TEST_* participant over the live deployed HTTP API (entry via the
Connect ``?batch=`` adapter through consent -> training -> connecting -> task), then:
  1. exercises a real ``record_share`` command (durable path unchanged),
  2. POSTs a ``/telemetry`` batch mid-task carrying all seven case-file kinds
     (file_meta + view/dwell/scroll/reorder/order_final/share_attempt) in the
     exact shapes the deployed bundle emits, with a ``client_epoch``, plus one
     legacy kind,
  3. keeps driving until the task closes, then POSTs the departure tail
     (final dwell + final order) — the pagehide path — and asserts it landed,
  4. asserts rows in ``app.event`` carry non-NULL ``active_ms``, the flush-time
     ``stage``, and the persisted ``payload.client_epoch``.

Synthetic-only evidence; never touches paid intake. Run while ``study.smoke.v11``
enrollment is open:

    uv run --project backend --frozen python scripts/qa_smoke_telemetry.py
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://investigators.local/investigators/api"
OUT = ROOT / "docs/evidence/2026-10-12-case-file-telemetry"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://investigators_user:investigators_user@127.0.0.1:5432/investigators_db",
)

COMPREHENSION = {
    "partial_intel": True,
    "same_crates": False,
    "accurate_not_latest": True,
    "teammates_may_have_mine": True,
                "record_before_timer": True,
    "assistant_reads_team": False,
}


def main() -> int:
    tag = secrets.token_hex(4).upper()
    pid = f"TEST_QA_TELEM_{tag}"
    client = httpx.Client(base_url=BASE, timeout=30.0)
    log: list[dict[str, object]] = []

    def note(step: str, **kw: object) -> None:
        log.append({"t": time.time(), "step": step, **kw})

    r = client.post(
        "/entry",
        params={
            "batch": "study.smoke.v11",
            "participantId": pid,
            "projectId": "qa-telemetry",
            "assignmentId": pid,
        },
    )
    r.raise_for_status()
    sid = r.json()["session_id"]
    note("entry", session_id=sid)
    csrf = client.cookies.get("inv_csrf")
    writer = client.cookies.get("inv_writer")
    headers = {"x-csrf-token": csrf or "", "x-writer-id": writer or ""}

    def session() -> dict:
        return client.get("/session").json()["session"]

    def workbench() -> dict:
        return client.get("/workbench").json()

    def command(kind: str, payload: dict | None = None, *, retry_reject: int = 0) -> dict:
        for attempt in range(retry_reject + 1):
            r = client.post(
                "/commands",
                json={"kind": kind, "payload": payload or {}},
                headers=headers,
            )
            body = r.json() if r.content else {}
            if r.status_code == 200 and body.get("accepted"):
                return body
            reason = body.get("reason") or body.get("error") or str(body)
            if attempt < retry_reject and reason in (
                "connecting_incomplete",
                "writer_lease_held",
            ):
                time.sleep(1.5)
                continue
            raise RuntimeError(f"{kind} rejected: {r.status_code} {body}")
        raise AssertionError("unreachable")

    # Drive to the task stage.
    asked = False
    shared_id = None
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        stage = session()["stage"]
        note("stage", stage=stage)
        if stage == "entry":
            time.sleep(1)
            continue
        wb = workbench()
        if stage == "consent":
            key = (wb.get("material") or {}).get("artifact_key")
            command("consent", {"action": "consented", "text_version": key})
        elif stage == "instructions":
            command("set_initials", {"initials": "TQ"})
            command("training_step", {"step": "instructions"})
        elif stage == "training":
            records = (wb.get("practice") or {}).get("records") or []
            command(
                "training_step",
                {
                    "step": "practice",
                    "action": "share",
                    "record_id": records[0]["record_id"],
                },
            )
            command(
                "training_step",
                {
                    "step": "practice",
                    "action": "pick_location",
                    "crate": 3, "location": "The Sawmill",
                },
            )
            if not asked:
                command(
                    "training_step",
                    {
                        "step": "practice",
                        "action": "ask",
                        "text": "Do you have any intel on crate 3?",
                    },
                )
                asked = True
            command(
                "training_step",
                {"step": "comprehension", "answers": COMPREHENSION, "complete": True},
            )
        elif stage == "scripted_connecting":
            command("ready", retry_reject=15)
        elif stage in ("ready", "solo_review", "checkin_wait"):
            time.sleep(1)
            continue
        elif stage == "task":
            records = wb.get("file") or []
            if not records:
                raise RuntimeError("task workbench returned an empty file")
            shared_id = records[0]["record_id"]
            note("task_reached", record_count=len(records), first=shared_id)
            break
        elif stage == "checkin_collect":
            command("pressure_response", {"value": 5})
        elif stage in ("checkin_reveal", "answers_locked"):
            command("proceed")
        else:
            time.sleep(1)
        time.sleep(0.5)
    else:
        raise RuntimeError("timed out before task stage")

    # 1) Real share command (durable path).
    if shared_id:
        try:
            command("record_share", {"record_id": shared_id})
            note("record_share", record_id=shared_id, accepted=True)
        except RuntimeError as exc:
            note("record_share", record_id=shared_id, accepted=False, error=str(exc))

    # 2) Mid-task telemetry batch — every case-file kind in the deployed
    # bundle's payload shapes, stamped with a client_epoch (cb-1.8).
    epoch = f"smoke-{tag.lower()}"
    t0 = 123_000

    def item(kind: str, client_ms: int, data: dict | None = None) -> dict:
        return {
            "kind": kind,
            "client_monotonic_ms": client_ms,
            "client_epoch": epoch,
            "data": data or {},
        }

    n_file = len(workbench().get("file") or []) or 1
    events = [
        item("file_meta", t0 + 5, {"io_supported": True, "n_items": n_file}),
        item(
            "file_record_view",
            t0 + 10,
            {"record_id": shared_id, "crate": 1, "source": "initial"},
        ),
        item(
            "file_record_dwell",
            t0 + 900,
            {"record_id": shared_id, "dwell_ms": 890, "final": False},
        ),
        item(
            "file_scroll_summary",
            t0 + 5000,
            {
                "depth_frac": 0.6,
                "max_depth_frac": 0.97,
                "farthest_index": n_file - 1,
                "n_items": n_file,
                "direction_reversals": 2,
                "distance_px": 4200,
            },
        ),
        item(
            "file_reorder",
            t0 + 6000,
            {
                "record_id": shared_id,
                "method": "drag",
                "from_index": 0,
                "to_index": 3,
            },
        ),
        item(
            "share_attempt",
            t0 + 7000,
            {"record_id": shared_id, "via": "drag", "result": "sent"},
        ),
        item("visibility", t0 + 8000, {"state": "hidden"}),
        item(
            "file_order_final",
            t0 + 9000,
            {"order": [shared_id] + [f"r{i}" for i in range(5)], "n_items": n_file},
        ),
    ]
    r = client.post(
        "/telemetry",
        json={"kind": "batch", "data": {"events": events}},
        headers=headers,
    )
    r.raise_for_status()
    note("telemetry_post", status=r.status_code, body=r.json(), count=len(events))

    # 3) Drive on until the task closes, then post the departure tail — the
    # pagehide finalizer path (final dwell + final order) must still land.
    close_deadline = time.monotonic() + 700
    post_close_stage = None
    while time.monotonic() < close_deadline:
        stage = session()["stage"]
        note("stage", stage=stage)
        if stage in ("task", "solo_review", "checkin_wait"):
            time.sleep(2)
            continue
        if stage == "checkin_collect":
            command("pressure_response", {"value": 5})
        elif stage in ("checkin_reveal", "answers_locked"):
            command("proceed")
        else:
            post_close_stage = stage
            break
        time.sleep(0.5)

    departure = [
        item(
            "file_record_dwell",
            t0 + 295_000,
            {"record_id": shared_id, "dwell_ms": 1200, "final": True},
        ),
        item(
            "file_order_final",
            t0 + 295_100,
            {"order": [shared_id] + [f"r{i}" for i in range(5)], "n_items": n_file},
        ),
    ]
    r = client.post(
        "/telemetry",
        json={"kind": "batch", "data": {"events": departure}},
        headers=headers,
    )
    r.raise_for_status()
    note("departure_post", status=r.status_code, stage=post_close_stage)

    time.sleep(1.0)

    # 4) Verify persisted rows: types, non-NULL active_ms, flush-time stage,
    # and the persisted client_epoch.
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "select type, stage, active_ms, payload "
                    "from app.event where session_id = :sid and type like 'telemetry_%' "
                    "order by seq"
                ),
                {"sid": sid},
            )
            .mappings()
            .all()
        )
        share = conn.execute(
            text(
                "select count(*) as n from app.event where session_id=:sid and type='record_shared'"
            ),
            {"sid": sid},
        ).scalar()

    by_type: dict[str, int] = {}
    missing_active = []
    missing_epoch = []
    stages: dict[str, list[str]] = {}
    for row in rows:
        by_type[row["type"]] = by_type.get(row["type"], 0) + 1
        if row["active_ms"] is None:
            missing_active.append(row["type"])
        if (row["payload"] or {}).get("client_epoch") != epoch:
            missing_epoch.append(row["type"])
        stages.setdefault(row["type"], [])
        if row["stage"] not in stages[row["type"]]:
            stages[row["type"]].append(row["stage"])
    note(
        "persisted_telemetry",
        counts=by_type,
        missing_active_ms=missing_active,
        missing_epoch=missing_epoch,
        stages=stages,
    )
    note("record_shared_events", count=share)

    expected = {
        "telemetry_file_meta",
        "telemetry_file_record_view",
        "telemetry_file_record_dwell",
        "telemetry_file_scroll_summary",
        "telemetry_file_reorder",
        "telemetry_file_order_final",
        "telemetry_share_attempt",
        "telemetry_visibility",
    }
    persisted = set(by_type)
    # The departure batch must land post-close (G02): the drive waits for the
    # task to close before posting, so order_final/dwell appear twice and the
    # second set carries a post-task flush-time stage.
    departed_post_close = any(st != "task" for st in stages.get("telemetry_file_order_final", []))
    ok = (
        expected.issubset(persisted)
        and not missing_active
        and not missing_epoch
        and share >= 1
        and by_type.get("telemetry_file_order_final", 0) == 2
        and by_type.get("telemetry_file_record_dwell", 0) == 2
        and post_close_stage is not None
        and departed_post_close
    )

    evidence = {
        "participant_id": pid,
        "session_id": sid,
        "batch": "study.smoke.v11",
        "client_epoch": epoch,
        "post_close_stage": post_close_stage,
        "expected": sorted(expected),
        "persisted": by_type,
        "stages": stages,
        "missing_active_ms": missing_active,
        "missing_epoch": missing_epoch,
        "record_shared_events": share,
        "log": log,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"telemetry-smoke-{tag}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in evidence.items() if k != "log"}, indent=2))
    print(f"evidence: {path}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
