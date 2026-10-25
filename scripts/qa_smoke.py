#!/usr/bin/env python3
"""Parametrized full-completion smoke for any round (audit M2; replaces the
copy-per-round ``qa_smoke_v1x*.py`` scripts from v15 on).

Mints (idempotently) one member batch per cell plus a ``test_only`` Prolific
group with ``target_completers_per_cell=1``, opens it, drives synthetic TEST_*
participants through the real Prolific entry shape and full lifecycle
(consent → instructions → training [share + pick + comprehension] → ai_qa →
connecting → task → instruments → debrief → data-use → ``/complete``), verifies
the completion code, and re-closes the group on exit. Never touches
``study.live.*``.

Comprehension answers are derived from the backend's own
``lifecycle.comprehension_key`` and the session's ``ai_assistant`` posture, so a
new comprehension item never needs a script edit (v14 needed a patch script).

    uv run --project backend --frozen python scripts/qa_smoke.py \\
        --group study.smoke.v15 \\
        --cell none=tp_ai_ovnone_v3 --cell none_thin=tp_ai_ovnone_thin_v3 \\
        --cell some=tp_ai_ovsome_v3 --cell some_thin=tp_ai_ovsome_thin_v3 \\
        --expected-code PLACEHOLDER-CODE --consent-key consent.reference.nobonus@v1 \\
        --out docs/evidence/<date>-prolific-v15-smoke
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _adminclient import ROOT, AdminClient

from app.services.lifecycle import comprehension_key

PARTICIPANT_BASE = "https://investigators.local/investigators/api"


@dataclass
class Config:
    group: str
    cells: dict[str, str]
    expected_code: str
    consent_key: str | None
    out: Path
    runs: int
    base: str = PARTICIPANT_BASE
    overlap: str = "independent"


@dataclass
class Run:
    pid: str
    log: list[dict[str, Any]] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)

    def note(self, step: str, **kw: Any) -> None:
        self.log.append({"t": time.time(), "step": step, **kw})


def mint(admin: AdminClient, cfg: Config, run: Run) -> None:
    for suffix, manifest_id in cfg.cells.items():
        batch_id = f"{cfg.group}.{suffix}"
        r = admin.post(
            "/admin/batches", {"batch_id": batch_id, "manifest_id": manifest_id, "mode": "live"}
        )
        if r.status_code != 409:
            r.raise_for_status()
        run.note("batch", batch_id=batch_id, status=r.status_code)
    r = admin.post(
        "/admin/batch-groups",
        {
            "group_id": cfg.group,
            "batch_ids": [f"{cfg.group}.{s}" for s in cfg.cells],
            "mode": "live",
            "enrollment_target": {
                "platform": "prolific",
                "target_completers_per_cell": 1,
                "test_only": True,
            },
        },
    )
    if r.status_code != 409:
        r.raise_for_status()
    run.note("group", status=r.status_code)


def set_enrollment(admin: AdminClient, cfg: Config, run: Run, open_: bool) -> None:
    r = admin.post(
        f"/admin/batch-groups/{cfg.group}/enrollment",
        {"open": open_, "reason": f"qa_smoke {run.pid}"},
    )
    if r.status_code != 200:
        # e.g. 409 quota_target_reached: a test_only group holds one completer per
        # cell, so each smoke round needs a fresh --group (study.smoke.vNN[-suffix]).
        raise RuntimeError(f"enrollment {open_} refused: {r.status_code} {r.text}")
    if r.json().get("enrollment_open") is not open_:
        raise RuntimeError("enrollment response did not confirm the requested state")
    run.note("enrollment", open=open_)


class Participant:
    def __init__(self, cfg: Config, run: Run) -> None:
        self.cfg, self.run = cfg, run
        self.client = httpx.Client(base_url=cfg.base, timeout=30.0)
        self.headers: dict[str, str] = {}

    def enter(self) -> str:
        r = self.client.post(
            "/entry",
            params={
                "batch": self.cfg.group,
                "PROLIFIC_PID": self.run.pid,
                "STUDY_ID": f"qa-smoke-{self.cfg.group}",
                "SESSION_ID": self.run.pid,
            },
        )
        r.raise_for_status()
        self.headers = {
            "x-csrf-token": self.client.cookies.get("inv_csrf") or "",
            "x-writer-id": self.client.cookies.get("inv_writer") or "",
        }
        return str(r.json()["session_id"])

    def session(self) -> dict[str, Any]:
        return dict(self.client.get("/session").json()["session"])

    def workbench(self) -> dict[str, Any]:
        return dict(self.client.get("/workbench").json())

    def command(self, kind: str, payload: dict[str, Any] | None = None, *, retries: int = 0) -> Any:
        for attempt in range(retries + 1):
            r = self.client.post(
                "/commands", json={"kind": kind, "payload": payload or {}}, headers=self.headers
            )
            body = r.json() if r.content else {}
            if r.status_code == 200 and body.get("accepted"):
                return body
            reason = body.get("reason") or body.get("error")
            if attempt < retries and reason in ("connecting_incomplete", "writer_lease_held"):
                time.sleep(1.5)
                continue
            raise RuntimeError(f"{kind} rejected: {r.status_code} {body}")
        raise AssertionError("unreachable")

    def answer_instruments(self, stage: str, wb: dict[str, Any]) -> None:
        inst = wb.get("instrument") or {}
        items = inst.get("items") or []
        if stage == "confidence":
            for crate in inst.get("pending_crates") or []:
                self.command(
                    "item_response", {"item_id": items[0]["item_id"], "crate": crate, "value": 60}
                )
            return
        free_text = (inst.get("response") or {}).get("type") == "free_text"
        answered = set(inst.get("answered_items") or [])
        for item in items:
            base = item["item_id"]
            value: Any = (
                f"QA smoke free-text response for {base}."
                if item.get("free_text") or free_text
                else (item["options"][0] if item.get("options") else 4)
            )
            for seat in inst.get("seats") or [None]:
                if (f"{base}.{seat}" if seat else base) in answered:
                    continue
                payload: dict[str, Any] = {"item_id": base, "value": value}
                if seat:
                    payload["seat"] = seat
                self.command("item_response", payload)

    def check_consent(self, wb: dict[str, Any]) -> str:
        material = wb.get("material") or {}
        key = str(material.get("artifact_key"))
        if self.cfg.consent_key and key != self.cfg.consent_key:
            raise RuntimeError(f"consent material {key!r} != {self.cfg.consent_key!r}")
        if wb.get("bonus_payment") is False:
            text = json.dumps((material.get("body") or {}).get("text") or {}).lower()
            if "$0.50" in text or "bonus" in text:
                raise RuntimeError("no-bonus consent body still mentions the bonus")
        return key

    def drive(self) -> str | None:
        """Walk the lifecycle to ``data_use_choice``, then ``/complete``; return the code."""
        practice_asked = qa_asked = answered_task = False
        last_stage = None
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            stage = self.session()["stage"]
            if stage != last_stage:
                self.run.note("stage", stage=stage)
                self.run.stages.append(stage)
                last_stage = stage
            if stage == "completion":
                break
            if stage == "entry":
                time.sleep(1)
                continue
            wb = self.workbench()
            if stage == "consent":
                self.command(
                    "consent", {"action": "consented", "text_version": self.check_consent(wb)}
                )
            elif stage == "instructions":
                self.command("set_initials", {"initials": "TQ"})
                self.command("training_step", {"step": "instructions"})
            elif stage == "training":
                records = (wb.get("practice") or {}).get("records") or []
                self.command(
                    "training_step",
                    {"step": "practice", "action": "share", "record_id": records[0]["record_id"]},
                )
                self.command(
                    "training_step",
                    {
                        "step": "practice",
                        "action": "pick_location",
                        "crate": 3,
                        "location": "The Sawmill",
                    },
                )
                if not practice_asked:
                    self.command(
                        "training_step",
                        {"step": "practice", "action": "ask", "text": "Any intel on crate 3?"},
                    )
                    practice_asked = True
                answers = comprehension_key(
                    ai_assistant=bool(wb.get("ai_assistant", True)), overlap=self.cfg.overlap
                )
                res = self.command(
                    "training_step",
                    {"step": "comprehension", "answers": answers, "complete": True},
                )
                if res.get("missed"):
                    raise RuntimeError(f"comprehension missed: {res['missed']}")
            elif stage == "ai_qa":
                exchanges = wb.get("qa")
                if exchanges is None:
                    raise RuntimeError("qa projection null on the ai_qa stage")
                if not qa_asked:
                    self.command("qa_ask", {"question": "How many crates am I assigned?"})
                    qa_asked = True
                    continue
                latest = exchanges[-1] if exchanges else {}
                if latest.get("status") not in ("answered", "fallback"):
                    time.sleep(2)
                    continue
                if not latest.get("answer"):
                    raise RuntimeError(f"qa exchange resolved without answer: {latest}")
                self.command("proceed")
            elif stage == "scripted_connecting":
                self.command("ready", retries=15)
            elif stage in ("ready", "solo_review", "checkin_wait"):
                time.sleep(1)
                continue
            elif stage == "task":
                if not answered_task and wb.get("world_ready"):
                    location = (wb.get("locations") or [])[0]
                    for crate in wb.get("assigned_crates") or []:
                        self.command("answer_update", {"crate": crate, "location_id": location})
                    answered_task = True
                time.sleep(2)
                continue
            elif stage == "checkin_collect":
                self.command("pressure_response", {"value": 5})
            elif stage in ("checkin_reveal", "answers_locked"):
                self.command("proceed")
            elif stage in ("confidence", "peer_ratings", "post_questionnaire", "open_ended"):
                self.answer_instruments(stage, wb)
                if self.session()["stage"] == stage:
                    self.command("proceed")
            elif stage == "debrief":
                self.command("debrief_ack")
            elif stage == "data_use_choice":
                self.command("data_use_choice", {"choice": "use_all"})
                break
            else:
                raise RuntimeError(f"unexpected stage {stage}")
            time.sleep(0.5)
        else:
            raise RuntimeError(f"timed out in stage {last_stage}")
        r = self.client.post("/complete", json={"kind": "complete"}, headers=self.headers)
        complete = r.json()
        if self.session()["stage"] != "completion":
            raise RuntimeError(f"final stage {self.session()['stage']}")
        code = (complete.get("result") or {}).get("completion_code") or complete.get(
            "completion_code"
        )
        return str(code) if code is not None else None


def one_run(admin: AdminClient, cfg: Config, index: int) -> bool:
    run = Run(pid=f"TEST_SMOKE_{cfg.group.replace('.', '_').upper()}_{secrets.token_hex(4)}")
    evidence: dict[str, Any] = {"participant_id": run.pid, "batch_group": cfg.group, "run": index}
    ok, closed = False, False
    try:
        mint(admin, cfg, run)
        set_enrollment(admin, cfg, run, True)
        participant = Participant(cfg, run)
        evidence["session_id"] = participant.enter()
        code = participant.drive()
        evidence["returned_code"] = code
        if code != cfg.expected_code:
            raise RuntimeError(f"completion code {code!r} != {cfg.expected_code!r}")
        ok = True
    except Exception as exc:  # noqa: BLE001 - the failure is recorded as evidence
        evidence["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        for attempt in range(3):
            try:
                set_enrollment(admin, cfg, run, False)
                closed = True
                break
            except Exception as exc:  # noqa: BLE001 - retain failed cleanup attempts
                run.note("enrollment_close_error", attempt=attempt + 1, error=str(exc))
                time.sleep(1)
    ok = ok and closed
    evidence.update(enrollment_closed=closed, stages_observed=run.stages, ok=ok, log=run.log)
    cfg.out.mkdir(parents=True, exist_ok=True)
    path = cfg.out / f"qa-smoke-{cfg.group}-{run.pid}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(f"run {index}: {'PASS' if ok else 'FAIL ' + str(evidence.get('failure'))} → {path}")
    return ok


def parse_args(argv: list[str]) -> Config:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--group", required=True, help="test-only group id (study.smoke.*)")
    ap.add_argument("--cell", action="append", required=True, metavar="SUFFIX=MANIFEST")
    ap.add_argument("--expected-code", required=True)
    ap.add_argument("--consent-key", default=None, help="assert the served consent artifact")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--runs", type=int, default=None, help="default: one per cell")
    ap.add_argument("--overlap", default="independent", help="'full' flips same_crates")
    ap.add_argument("--base", default=PARTICIPANT_BASE)
    a = ap.parse_args(argv)
    if not a.group.startswith("study.smoke."):
        ap.error("--group must be a study.smoke.* test-only group")
    cells = dict(c.split("=", 1) for c in a.cell)
    out = a.out if a.out.is_absolute() else ROOT / a.out
    return Config(
        group=a.group,
        cells=cells,
        expected_code=a.expected_code,
        consent_key=a.consent_key,
        out=out,
        runs=a.runs or len(cells),
        base=a.base,
        overlap=a.overlap,
    )


def main(argv: list[str]) -> int:
    cfg = parse_args(argv)
    admin = AdminClient.login()
    results = [one_run(admin, cfg, i + 1) for i in range(cfg.runs)]
    print(f"{sum(results)}/{len(results)} PASS")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
