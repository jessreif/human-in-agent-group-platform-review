#!/usr/bin/env python3
"""Post-deploy smoke for the AI_QA_ENABLED gate on study.smoke.v11.

Drives one synthetic TEST_* participant over the live deployed HTTP API far enough
to prove two things on the current build:

1. A mutating command path still works end-to-end (entry -> consent ->
   instructions -> practice share/ask -> comprehension).
2. The deployed ``AI_QA_ENABLED`` value routes training correctly: flag=0 exits
   ``training`` straight into ``scripted_connecting``; flag=1 lands on the
   ``ai_qa`` stage with a non-null ``qa`` workbench projection, accepts a
   ``qa_ask`` command, resolves the exchange to ``answered``/``fallback``, and
   ``proceed`` continues into ``scripted_connecting``. Either way ``ready`` is
   accepted and the session reaches ``task``.

The deployed flag is read from ``/admin/preview-combos`` so the same script
verifies both postures. The smoke batch group is opened for the run and closed
again on exit (success or failure). Synthetic-only evidence; never touches
paid intake.

Run from the repo root against the deployed stack:

    uv run --project backend --frozen python scripts/qa_smoke_aiqa_gate.py
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
GROUP = "study.smoke.v11"
OUT = ROOT / "docs/evidence/2026-10-14-ai-qa-deploy"


def _admin_password() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("ADMIN_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("ADMIN_PASSWORD not found in .env")


def main() -> int:
    tag = secrets.token_hex(4).upper()
    pid = f"TEST_QA_AIQA_{tag}"
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
    # The admin cookies are set Secure + Path=/investigators/ for the public
    # deployment; over the host-local container port neither matches, so carry
    # them explicitly instead of relying on the cookie jar.
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
    r = admin.get("/admin/preview-combos", headers=admin_headers)
    r.raise_for_status()
    flag_on = bool(r.json().get("ai_qa_enabled"))
    note("deployed_flag", ai_qa_enabled=flag_on)

    def set_enrollment(open_: bool) -> None:
        r = admin.post(
            f"/admin/batch-groups/{GROUP}/enrollment",
            json={"open": open_, "reason": f"post-deploy ai_qa gate smoke {tag}"},
            headers=admin_headers,
        )
        r.raise_for_status()
        response = r.json()
        note("enrollment", open=open_, response=response)
        if response.get("enrollment_open") is not open_:
            raise RuntimeError("Enrollment response did not confirm the requested state")

    client = httpx.Client(base_url=BASE, timeout=30.0)
    ok = False
    failure: str | None = None
    sid = ""
    qa_outcome: dict[str, object] = {}
    enrollment_closed = False
    try:
        set_enrollment(True)

        r = client.post(
            "/entry",
            params={
                "batch": GROUP,
                "participantId": pid,
                "projectId": "qa-smoke",
                "assignmentId": pid,
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
            "assistant_reads_team": False,
        }

        deadline = time.monotonic() + 300
        last_stage = None
        practice_asked = False
        qa_asked = False
        while time.monotonic() < deadline:
            stage = session()["stage"]
            if stage != last_stage:
                note("stage", stage=stage)
                stages.append(stage)
                last_stage = stage
            if stage == "task":
                break
            if stage == "ai_qa" and not flag_on:
                raise RuntimeError(
                    "ai_qa stage reached while AI_QA_ENABLED=0 — flag-off path broken"
                )
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
                command(
                    "training_step",
                    {
                        "step": "comprehension",
                        "answers": COMPREHENSION,
                        "complete": True,
                    },
                )
            elif stage == "ai_qa":
                exchanges = wb.get("qa") or []
                if wb.get("qa") is None:
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
                elif time.monotonic() + 90 < deadline:
                    time.sleep(2)
                    continue
                else:
                    raise RuntimeError(f"qa exchange never resolved: {latest}")
            elif stage == "scripted_connecting":
                command("ready", retry_reject=15)
            else:
                time.sleep(1)
                continue
            time.sleep(0.5)
        else:
            raise RuntimeError(f"timed out in stage {last_stage}")

        if last_stage != "task":
            raise RuntimeError(f"did not reach task (last stage {last_stage})")
        if "scripted_connecting" not in stages:
            raise RuntimeError(f"scripted_connecting never observed: {stages}")
        if flag_on:
            if "ai_qa" not in stages:
                raise RuntimeError(f"ai_qa never reached while flag on: {stages}")
            if qa_outcome.get("status") not in ("answered", "fallback"):
                raise RuntimeError(f"qa exchange unresolved: {qa_outcome}")
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
        "ai_qa_enabled_deployed": flag_on,
        "qa_outcome": qa_outcome or None,
        "enrollment_closed": enrollment_closed,
        "assertion": (
            "training -> ai_qa (qa_ask resolved) -> scripted_connecting -> ready -> task"
            if flag_on
            else "training -> scripted_connecting (ai_qa skipped); ready accepted; task reached"
        ),
        "stages_observed": stages,
        "failure": failure,
        "log": log,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"qa-smoke-aiqa-gate-{tag}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in evidence.items() if k != "log"}, indent=2))
    print(f"evidence: {path}")
    print("PASS" if ok else f"FAIL {failure}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
