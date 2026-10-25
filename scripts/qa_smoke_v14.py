#!/usr/bin/env python3
"""QA full-completion smoke on a fresh study.smoke.v14 group through the Prolific adapter.

Mirrors the fielded study.live.v14 contract (same v3 manifests — v14 adds the pick gate, comprehension item 6, top-ribbon answer progress, universal T-15s final-check notice, and the AI-QA initials fix) end to end with synthetic TEST_* traffic
only: mints four member batches bound to the v3 fielded manifests, groups them with
``platform="prolific"`` + ``test_only=True`` + ``target_completers_per_cell=1``,
opens enrollment, then drives one participant through the real Prolific entry shape
(``?batch=<group>&PROLIFIC_PID=...&STUDY_ID=...&SESSION_ID=...``) through consent ->
instructions -> training -> comprehension -> ai_qa -> scripted_connecting -> task ->
check-in -> instruments -> debrief -> data-use -> ``/complete``, and verifies the
Prolific completion code.

v13 addition (BONUS_PAYMENT_ENABLED=0): the consent stage asserts the served
material is ``consent.reference.nobonus@v1``, that ``$0.50``/bonus copy is absent
from the consent body, and that ``GET /workbench`` reports ``bonus_payment=false``.

The group is re-closed on exit (success or failure). Never touches paid intake or
study.live.* groups.

Run from the repo root against the deployed stack:

    uv run --project backend --frozen python scripts/qa_smoke_v14.py
"""

from __future__ import annotations

import json
import re
import secrets
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://investigators.local/investigators/api"
# /admin/* is Shibboleth-gated at the Apache layer; the container port serves the
# app at root for host-local admin calls.
ADMIN_BASE = "http://127.0.0.1:8519"
GROUP = "study.smoke.v14"
CELLS = {
    "none": "tp_ai_ovnone_v3",
    "none_thin": "tp_ai_ovnone_thin_v3",
    "some": "tp_ai_ovsome_v3",
    "some_thin": "tp_ai_ovsome_thin_v3",
}
EXPECTED_CODE = "PLACEHOLDER-CODE"
NOBONUS_CONSENT = "consent.reference.nobonus@v1"
OUT = ROOT / "docs/evidence/2026-10-16-prolific-v14-smoke"


def _admin_password() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("ADMIN_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("ADMIN_PASSWORD not found in .env")


def main() -> int:
    tag = secrets.token_hex(4).upper()
    pid = f"TEST_PROLIFIC_V14_{tag}"
    log: list[dict[str, object]] = []
    stages: list[str] = []

    def note(step: str, **kw: object) -> None:
        log.append({"t": time.time(), "step": step, **kw})

    admin = httpx.Client(base_url=ADMIN_BASE, timeout=30.0)
    r = admin.post(
        "/admin/login",
        json={"role": "researcher", "password": _admin_password()},
    )
    r.raise_for_status()
    note("admin_login", status=r.status_code)
    jar: dict[str, str] = {}
    for sc in r.headers.get_list("set-cookie"):
        for name in ("inv_admin", "inv_admin_csrf"):
            m = re.match(rf"\s*{name}=([^;,]+)", sc)
            if m:
                jar[name] = m.group(1)
    admin_headers = {
        "x-csrf-token": jar.get("inv_admin_csrf", ""),
        "cookie": "; ".join(f"{k}={v}" for k, v in jar.items()),
    }

    def set_enrollment(open_: bool) -> None:
        r = admin.post(
            f"/admin/batch-groups/{GROUP}/enrollment",
            json={"open": open_, "reason": f"prolific adapter smoke {tag}"},
            headers=admin_headers,
        )
        r.raise_for_status()
        response = r.json()
        note("enrollment", open=open_, response=response)
        if response.get("enrollment_open") is not open_:
            raise RuntimeError("Enrollment response did not confirm the requested state")

    # Mint the four member batches + group (idempotent enough for a rerun after a
    # partial failure: 409s on existing rows are treated as already-minted).
    batch_ids = [f"{GROUP}.{suffix}" for suffix in CELLS]
    for suffix, manifest_id in CELLS.items():
        r = admin.post(
            "/admin/batches",
            json={
                "batch_id": f"{GROUP}.{suffix}",
                "manifest_id": manifest_id,
                "mode": "live",
            },
            headers=admin_headers,
        )
        if r.status_code == 409:
            note("batch_exists", batch_id=f"{GROUP}.{suffix}")
        else:
            r.raise_for_status()
            note("batch_minted", batch_id=f"{GROUP}.{suffix}", manifest_id=manifest_id)
    r = admin.post(
        "/admin/batch-groups",
        json={
            "group_id": GROUP,
            "batch_ids": batch_ids,
            "mode": "live",
            "enrollment_target": {
                "platform": "prolific",
                "target_completers_per_cell": 1,
                "test_only": True,
            },
        },
        headers=admin_headers,
    )
    if r.status_code == 409:
        note("group_exists", group_id=GROUP)
    else:
        r.raise_for_status()
        note("group_minted", response=r.json())

    client = httpx.Client(base_url=BASE, timeout=30.0)
    ok = False
    failure: str | None = None
    sid = ""
    qa_outcome: dict[str, object] = {}
    nobonus: dict[str, object] = {}
    code: str | None = None
    enrollment_closed = False
    try:
        set_enrollment(True)

        r = client.post(
            "/entry",
            params={
                "batch": GROUP,
                "PROLIFIC_PID": pid,
                "STUDY_ID": "qa-smoke-v13",
                "SESSION_ID": pid,
            },
        )
        r.raise_for_status()
        entry = r.json()
        sid = entry["session_id"]
        note("entry", response=entry)
        headers = {
            "x-csrf-token": client.cookies.get("inv_csrf") or "",
            "x-writer-id": client.cookies.get("inv_writer") or "",
        }

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
        practice_asked = False
        qa_asked = False
        deadline = time.monotonic() + 900
        last_stage = None
        while time.monotonic() < deadline:
            sess = session()
            stage = sess["stage"]
            if stage != last_stage:
                note("stage", stage=stage, revision=sess.get("revision"))
                stages.append(stage)
                last_stage = stage
            if stage == "entry":
                time.sleep(1)
                continue
            if stage == "completion":
                break
            wb = workbench()
            if stage == "consent":
                material = wb.get("material") or {}
                key = material.get("artifact_key")
                if key != NOBONUS_CONSENT:
                    raise RuntimeError(f"consent material {key!r} != {NOBONUS_CONSENT!r}")
                if wb.get("bonus_payment") is not False:
                    raise RuntimeError(f"workbench bonus_payment {wb.get('bonus_payment')!r}")
                text = json.dumps(
                    (material.get("body") or {}).get("text") or {}
                ).lower()
                if "$0.50" in text or "bonus" in text:
                    raise RuntimeError("nobonus consent body still mentions the bonus")
                if "$3.00" not in text:
                    raise RuntimeError("nobonus consent body missing $3.00 flat rate")
                nobonus.update(
                    {
                        "consent_key": key,
                        "bonus_payment": False,
                        "flat_rate_ok": True,
                    }
                )
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
                if not practice_asked:
                    command(
                        "training_step",
                        {
                            "step": "practice",
                            "action": "ask",
                            "text": "Do you have any intel on crate 3?",
                        },
                    )
                    practice_asked = True
                res = command(
                    "training_step",
                    {
                        "step": "comprehension",
                        "answers": COMPREHENSION,
                        "complete": True,
                    },
                )
                if res.get("missed"):
                    raise RuntimeError(f"comprehension missed: {res['missed']}")
            elif stage == "ai_qa":
                exchanges = wb.get("qa")
                if exchanges is None:
                    raise RuntimeError("qa projection null on the ai_qa stage")
                if not qa_asked:
                    command(
                        "qa_ask",
                        {"question": "How many crates am I assigned to answer for?"},
                    )
                    qa_asked = True
                    note("qa_asked")
                    continue
                latest = exchanges[-1] if exchanges else {}
                st = latest.get("status")
                if st in ("answered", "fallback"):
                    if not latest.get("answer"):
                        raise RuntimeError(f"qa exchange resolved without answer: {latest}")
                    qa_outcome = {"status": st, "answer_len": len(latest["answer"])}
                    note("qa_resolved", **qa_outcome)
                    command("proceed")
                else:
                    time.sleep(2)
                    continue
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
            elif stage in ("checkin_reveal", "answers_locked"):
                command("proceed")
            elif stage in (
                "confidence",
                "peer_ratings",
                "post_questionnaire",
                "open_ended",
            ):
                answer_instruments(sess, wb)
                if session()["stage"] == stage:
                    command("proceed")
            elif stage == "debrief":
                command("debrief_ack")
            elif stage == "data_use_choice":
                # Records the disposition only; the transition to ``completion``
                # happens in POST /complete below (mirrors qa_smoke_v12).
                command("data_use_choice", {"choice": "use_all"})
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
        code = (complete.get("result") or {}).get("completion_code") or complete.get(
            "completion_code"
        )
        if final["stage"] != "completion":
            raise RuntimeError(f"final stage {final['stage']}")
        if code != EXPECTED_CODE:
            raise RuntimeError(f"completion code {code!r} != {EXPECTED_CODE!r}")
        ok = True
    except Exception as exc:  # noqa: BLE001 - record the failure as evidence too
        failure = f"{type(exc).__name__}: {exc}"
        note("failure", error=failure)
    finally:
        for attempt in range(3):
            try:
                set_enrollment(False)
                enrollment_closed = True
                break
            except Exception as exc:  # noqa: BLE001 - retain failed cleanup attempts
                note("enrollment_close_error", attempt=attempt + 1, error=str(exc))
                if attempt < 2:
                    time.sleep(1)
        if not enrollment_closed:
            ok = False
            failure = failure or "Smoke enrollment could not be re-closed"

    evidence = {
        "participant_id": pid,
        "session_id": sid,
        "batch_group": GROUP,
        "qa_outcome": qa_outcome or None,
        "nobonus_assertions": nobonus or None,
        "expected_prolific_code": EXPECTED_CODE,
        "returned_code": code,
        "stages_observed": stages,
        "enrollment_closed": enrollment_closed,
        "failure": failure,
        "log": log,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"qa-smoke-v14-prolific-{tag}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in evidence.items() if k != "log"}, indent=2))
    print(f"evidence: {path}")
    print("PASS" if ok else f"FAIL {failure}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
