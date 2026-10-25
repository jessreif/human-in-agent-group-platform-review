#!/usr/bin/env python3
"""Owner-authorized synthetic browser smoke on the v4 group; never opens paid intake.

Uses isolated agent-browser sessions and real UI actions, clocks and worker/model.
Retains synthetic records for separately authorized cleanup. No auth values are read.
Run only after deployment and opening study.smoke.v4, from the project root.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/evidence/2026-10-08-risk-remediation/browser"
STAMP = secrets.token_hex(4)
PREFIX = os.environ.get("AGENT_BROWSER_SESSION") or f"risk-smoke-{STAMP}"


def browser(session: str, *args: str, stdin: str | None = None):
    result = subprocess.run(
        ["agent-browser", "--session", session, "--json", *args],
        input=stdin, text=True, capture_output=True, timeout=100, check=True,
    )
    response = json.loads(result.stdout)
    if not response.get("success", True):
        raise RuntimeError(response)
    return response.get("data", response)


def observe(session: str):
    return browser(
        session, "eval", "--stdin",
        stdin="""JSON.stringify({
          text: document.body.innerText,
          inputs: [...document.querySelectorAll('input')].map(e =>
            ({id:e.id,type:e.type,disabled:e.disabled,value:e.value})),
          ranges: document.querySelectorAll('input[type=range]').length,
          likerts: document.querySelectorAll('.likert-row').length,
          likertsMeta: [...document.querySelectorAll('.likert-row')].map(e =>
            ({label:e.getAttribute('aria-label')})),
          choices: document.querySelectorAll('.choice-list').length,
          rangesMeta: [...document.querySelectorAll('input[type=range]')].map(e =>
            ({label:e.getAttribute('aria-label')})),
          answerSelects: [...document.querySelectorAll('.crate-picker select')].map(e =>
            ({id:e.id,option:e.options[1]?.value})),
          code: document.querySelector('[data-testid=completion-code]')?.textContent,
          overlay: !!document.querySelector('[data-testid=checkin-overlay]'),
          resumeEnabled: !!document.querySelector('[data-testid=checkin-overlay] button.primary:not(:disabled)'),
          buttons: [...document.querySelectorAll('button')].map(e =>
            ({text:e.textContent.trim(),disabled:e.disabled}))
        })""",
    )


def state(session: str) -> dict:
    data = observe(session)
    raw = data.get("result", data) if isinstance(data, dict) else data
    return json.loads(raw) if isinstance(raw, str) else raw


def wait_text(session: str, text: str, timeout: int = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if text in state(session)["text"]:
            return
        time.sleep(1)
    raise RuntimeError(f"{session}: did not reach {text}")


def click(session: str, text: str) -> None:
    # Semantic clicks can report success on disabled buttons. Wait for writer
    # initialization/busy state to clear, not just for the label to render.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(b["text"] == text and not b["disabled"] for b in state(session)["buttons"]):
            break
        time.sleep(0.25)
    else:
        raise RuntimeError(f"{session}: button never enabled: {text}")
    browser(session, "snapshot", "-i")
    browser(session, "find", "role", "button", "click", "--name", text, "--exact")


def visible_click(session: str, selector: str) -> None:
    browser(session, "scrollintoview", selector)
    # Smooth scrolling otherwise moves a long questionnaire's control between
    # locating it and sending the pointer click. Wait for its rectangle to settle.
    browser(session, "wait", "--fn", f"""
(async () => {{
  const e = document.querySelector({json.dumps(selector)});
  if (!e || e.disabled) return false;
  const a = e.getBoundingClientRect();
  await new Promise(r => setTimeout(r, 150));
  const b = e.getBoundingClientRect();
  return Math.abs(a.x-b.x) < 1 && Math.abs(a.y-b.y) < 1;
}})()
""")
    browser(session, "snapshot", "-i")
    browser(session, "click", selector)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sessions = [f"{PREFIX}-{STAMP}-{i}" for i in range(4)]
    pids = [f"TEST_RISK_{STAMP}_{i}" for i in range(4)]
    try:
        for session, pid in zip(sessions, pids):
            browser(session, "open",
                f"https://investigators.local/investigators/?batch=study.smoke.v4"
                f"&PROLIFIC_PID={pid}&STUDY_ID=test&SESSION_ID={pid}")
            wait_text(session, "Yes, I consent to participate")
            click(session, "Yes, I consent to participate")
            wait_text(session, "Continue to your workspace")
        # Read only these synthetic assignments. Never retrieve credentials or cookies.
        script = f"""
import json
from sqlalchemy import select
from app.db import schema as s
from app.db.engine import make_engine
from app.settings import Settings
engine=make_engine(Settings().DATABASE_URL.get_secret_value())
with engine.connect() as c:
    rows=c.execute(select(s.platform_link.c.platform_pid,s.session.c.session_id,
        s.session.c.manifest_id,s.session.c.arm).join(s.session,
        s.session.c.session_id==s.platform_link.c.session_id).where(
        s.platform_link.c.platform_pid.in_({pids!r}))).mappings()
    assignments=[dict(r) for r in rows]
    block=c.execute(select(s.session.c.manifest_id,s.session.c.arm,
        s.allocation.c.slot_index).join(s.allocation,
        s.allocation.c.session_id==s.session.c.session_id).where(
        s.session.c.batch_group_id=='study.smoke.v4',
        s.allocation.c.block_index==0)).mappings()
    print(json.dumps({{'assignments':assignments,
        'first_complete_block':[dict(r) for r in block]}},default=str))
engine.dispose()
"""
        raw = subprocess.check_output(
            ["docker", "exec", "-i", "investigators-api", "python", "-"],
            input=script, text=True,
        )
        result = json.loads(raw)
        assignments = result["assignments"]
        assert len(assignments) == 4
        block = result["first_complete_block"]
        assert len(block) == 4 and len({(r["manifest_id"], r["arm"]) for r in block}) == 4, block
        (OUT / "assignments.json").write_text(json.dumps(assignments, indent=2) + "\n")
        (OUT / "allocation-check.json").write_text(json.dumps(block, indent=2) + "\n")
        selected = {}
        for row in assignments:
            selected.setdefault(row["manifest_id"], sessions[pids.index(row["platform_pid"])])
        assert set(selected) == {"tp_ai_v3", "tp_scripted_v5"}
        for manifest, session in selected.items():
            browser(session, "fill", "#initials", "ZZ")
            click(session, "Continue to your workspace")
            wait_text(session, "Your workspace")
            click(session, "Continue")
            wait_text(session, "Practice: Sharing intel")
            browser(session, "snapshot", "-i")
            browser(session, "find", "first", ".file-actions button", "click")
            wait_text(session, "Shared in team chat")
            click(session, "Continue")
            wait_text(session, "Continue to your team")
            click(session, "Continue to your team")
            wait_text(session, "Start")
            click(session, "Start")
            wait_text(session, "Message the team")
            # A social question exercises model-backed replies, unlike fixed intel/probes.
            browser(session, "fill", "#chat-input", "What strategy are you using to compare your notes?")
            browser(session, "snapshot", "-i")
            browser(session, "click", '[data-pane=team-chat] .composer button')
            for answer in state(session)["answerSelects"]:
                browser(session, "select", f"#{answer['id']}", answer["option"])
            browser(session, "focus", "#chat-input")
            browser(session, "screenshot", str(OUT / f"{manifest}-task.png"))
            print(f"{manifest}: entered real-time task and sent social question", flush=True)
        completed = {}
        deadline = time.monotonic() + 900
        while len(completed) < 2 and time.monotonic() < deadline:
            for manifest, session in selected.items():
                if manifest in completed:
                    continue
                current = state(session)
                if current.get("code"):
                    completed[manifest] = current["code"].strip()
                    print(f"{manifest}: reached completion", flush=True)
                    # Preserve screenshot evidence without publishing the
                    # configured platform completion code.
                    browser(session, "eval", "--stdin", stdin="""
document.querySelector('[data-testid=completion-code]').style.filter = 'blur(12px)'
""")
                    browser(session, "screenshot", str(OUT / f"{manifest}-completion.png"))
                    browser(session, "open",
                        f"https://investigators.local/investigators/?batch=study.smoke.v4"
                        f"&PROLIFIC_PID={pids[sessions.index(session)]}"
                        f"&STUDY_ID=test&SESSION_ID={pids[sessions.index(session)]}")
                    wait_text(session, completed[manifest])
                    continue
                enabled = [b["text"] for b in current["buttons"] if not b["disabled"]]
                if current["overlay"]:
                    if "5" in enabled:
                        browser(session, "snapshot", "-i")
                        browser(session, "click", '[data-testid=checkin-overlay] .likert-row button:nth-child(6)')
                    elif current["resumeEnabled"]:
                        browser(session, "snapshot", "-i")
                        browser(session, "click", '[data-testid=checkin-overlay] button.primary')
                    continue
                if current["ranges"]:
                    for i in range(current["ranges"]):
                        label = current["rangesMeta"][i]["label"]
                        browser(session, "focus", f"input[type=range][aria-label={json.dumps(label)}]")
                        browser(session, "press", "ArrowRight")
                if current["likerts"]:
                    for i in range(current["likerts"]):
                        browser(session, "snapshot", "-i")
                        label = current["likertsMeta"][i]["label"]
                        visible_click(session, f".likert-row[aria-label={json.dumps(label)}] button:nth-child(4)")
                if current["choices"]:
                    browser(session, "snapshot", "-i")
                    browser(session, "click", ".choice-list button:first-child")
                if "Continue" in enabled:
                    click(session, "Continue")
                elif "Finish" in enabled:
                    click(session, "Finish")
            time.sleep(2)
        assert len(completed) == 2, {k: state(v)["text"] for k, v in selected.items()}
        (OUT / "completion.json").write_text(json.dumps(
            {m: {"reached": True, "reload_verified": True} for m in completed},
            indent=2,
        ) + "\n")
        print("Both successor builds reached and recovered completion codes:", list(completed), flush=True)
    finally:
        for session in sessions:
            subprocess.run(["agent-browser", "--session", session, "close"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
