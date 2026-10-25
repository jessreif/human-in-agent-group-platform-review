#!/usr/bin/env python3
"""QA full-completion smoke on study.smoke.v11 through Connect completion-code routing.

Drives one synthetic TEST_* participant over the live deployed HTTP API (entry via the
Connect ``?batch=`` adapter through consent -> training -> connecting -> task -> check-in
-> instruments -> debrief -> data-use -> ``/complete``), then verifies the completion
code routed for the member batch. Synthetic-only evidence; never opens paid intake.

Run from the repo root while ``study.smoke.v11`` enrollment is open:

    uv run --project backend --frozen python scripts/qa_smoke_v11.py
"""

from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://investigators.local/investigators/api"
EXPECTED_CODE = "PLACEHOLDER-CODE"  # CONNECT_COMPLETION for study.smoke.v11.{ai,ai2}
OUT = ROOT / "docs/evidence/2026-10-12-launch-v11/qa-smoke"


def main() -> int:
    tag = secrets.token_hex(4).upper()
    pid = f"TEST_QA_V11_{tag}"
    client = httpx.Client(base_url=BASE, timeout=30.0)
    log: list[dict[str, object]] = []

    def note(step: str, **kw: object) -> None:
        log.append({"t": time.time(), "step": step, **kw})

    r = client.post(
        "/entry",
        params={
            "batch": "study.smoke.v11",
            "participantId": pid,
            "projectId": "qa-smoke",
            "assignmentId": pid,
        },
    )
    r.raise_for_status()
    entry = r.json()
    sid = entry["session_id"]
    note("entry", response=entry)
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

    COMPREHENSION = {
        "partial_intel": True,
        "same_crates": False,
        "accurate_not_latest": True,
        "teammates_may_have_mine": True,
                "record_before_timer": True,
        "assistant_reads_team": False,
    }

    def answer_instruments(sess: dict, wb: dict) -> None:
        inst = wb.get("instrument") or {}
        rtype = (inst.get("response") or {}).get("type")
        items = inst.get("items") or []
        answered = set(inst.get("answered_items") or [])
        seats = inst.get("seats") or []
        if sess["stage"] == "confidence":
            for crate in inst.get("pending_crates") or []:
                command(
                    "item_response",
                    {"item_id": items[0]["item_id"], "crate": crate, "value": 60},
                )
            return
        for item in items:
            base = item["item_id"]
            if item.get("free_text") or rtype == "free_text":
                value: object = f"QA smoke free-text response for {base}."
            elif item.get("options"):
                value = item["options"][0]
            else:
                value = 4
            for seat in seats or [None]:
                key = f"{base}.{seat}" if seat else base
                if key in answered:
                    continue
                payload = {"item_id": base, "value": value}
                if seat:
                    payload["seat"] = seat
                command("item_response", payload)

    answered_task = False
    asked = False
    deadline = time.monotonic() + 900
    last_stage = None
    while time.monotonic() < deadline:
        sess = session()
        stage = sess["stage"]
        if stage != last_stage:
            note("stage", stage=stage, revision=sess.get("revision"))
            last_stage = stage
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
            practice = wb.get("practice") or {}
            records = practice.get("records") or []
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
            if not answered_task and wb.get("world_ready"):
                locs = wb.get("locations") or []
                for crate in wb.get("assigned_crates") or []:
                    command(
                        "answer_update",
                        {"crate": crate, "location_id": locs[0]},
                    )
                answered_task = True
                note("answers_submitted")
            time.sleep(2)
            continue
        elif stage == "checkin_collect":
            command("pressure_response", {"value": 5})
        elif stage == "checkin_reveal" or stage == "answers_locked":
            command("proceed")
        elif stage in (
            "confidence",
            "peer_ratings",
            "post_questionnaire",
            "open_ended",
        ):
            answer_instruments(sess, wb)
            # The final accepted item_response auto-advances a complete instrument
            # stage; only proceed when the stage did not move on its own.
            if session()["stage"] == stage:
                command("proceed")
        elif stage == "debrief":
            command("debrief_ack")
        elif stage == "data_use_choice":
            command("data_use_choice", {"choice": "use_all"})
            break
        elif stage == "completion":
            break
        else:
            raise RuntimeError(f"unexpected stage {stage}")
        time.sleep(0.5)
    else:
        raise RuntimeError(f"timed out in stage {last_stage}")

    r = client.post("/complete", json={"kind": "complete"}, headers=headers)
    complete = r.json()
    note("complete_response", response=complete)
    final = session()
    note("final_session", stage=final["stage"])

    code = (complete.get("result") or {}).get("completion_code") or complete.get("completion_code")
    evidence = {
        "participant_id": pid,
        "session_id": sid,
        "batch": "study.smoke.v11",
        "expected_connect_code": EXPECTED_CODE,
        "returned_code": code,
        "final_stage": final["stage"],
        "log": log,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"qa-smoke-v11-{tag}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in evidence.items() if k != "log"}, indent=2))
    print(f"evidence: {path}")
    ok = final["stage"] == "completion" and code == EXPECTED_CODE
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
