"""Evidence output for simulation runs.

Each run writes a directory under ``docs/evidence/<date>-agent-sim/run-<stamp>/``:

* ``summary.md`` — run metadata, per-session outcome table, all findings;
* ``sessions/<sid>.json`` — full transcript: every dispatched command + outcome,
  per-step persona actions, and the oracle findings for that session;
* ``replays/<sid>.json`` — the admin replay payload captured at run end, the
  exact shape ``GET /admin/sessions/{id}/replay`` serves;
* ``simulations/<sid>.html`` — standalone session-simulation files (only when
  the run passed ``--simulate`` and node is on PATH).

Everything needed to replay a scenario is in the JSON: manifest, persona, seed,
and the exact command sequence (the utterance engine is fully seeded when the
paraphraser is off).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import oracles
from .runner import Runner, SessionReport

REPO = Path(__file__).resolve().parents[2]


def _source_stamp() -> str:
    out = subprocess.run(
        ["python", "scripts/source_bundle.py"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or out.stderr.strip() or "unknown"


def write_report(
    reports: list[SessionReport],
    *,
    out_dir: Path | None = None,
    label: str | None = None,
    provider_requested: str | None = None,
    provider_effective: str | None = None,
) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = (
        out_dir
        if out_dir is not None
        else REPO / "docs" / "evidence" / f"{datetime.now(UTC).strftime('%Y-%m-%d')}-agent-sim"
    )
    # BA-88: two runs in the same second must not overwrite each other — claim an
    # exclusive directory (fail rather than merge after 100 collisions).
    run_id = f"run-{stamp}"
    out = base / run_id
    for suffix in range(1, 100):
        try:
            (out / "sessions").mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            run_id = f"run-{stamp}-{suffix}"
            out = base / run_id
    else:
        raise RuntimeError(f"could not allocate a run directory under {base}")

    source = _source_stamp()
    fails = sum(r.failed for r in reports)
    lines = [
        "# Agent-sim run",
        "",
        f"- stamp: `{stamp}`",
        f"- run id: `{run_id}`",
        f"- label: `{label or 'adhoc'}`",
        f"- source bundle: `{source}`",
        f"- oracle version: `{oracles.ORACLE_VERSION}`",
        f"- provider: requested `{provider_requested or '-'}`, "
        f"effective `{provider_effective or '-'}`",
        f"- sessions: {len(reports)}",
        f"- findings (fail severity): {fails}",
        "",
        "| persona | session | arm | terminal | reason | steps | fails |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        lines.append(
            f"| {r.persona} | `{r.session_id[:8]}` | {r.arm or '—'} | {r.stage} "
            f"| {r.terminal_reason or '—'} | {len(r.steps)} | {r.failed} |"
        )
    lines += ["", "## Findings", ""]
    for r in reports:
        for f in r.findings:
            lines.append(
                f"- **{f.severity.upper()}** `{f.check}` "
                f"({r.persona}, `{r.session_id[:8]}`): {f.detail}"
            )
            if f.evidence:
                lines.append(f"  - evidence: `{json.dumps(f.evidence)[:300]}`")
    for r in reports:
        d = asdict(r)
        replay = d.pop("replay", None)
        (out / "sessions" / f"{r.session_id}.json").write_text(
            json.dumps(d, indent=2, default=str),
            encoding="utf-8",
        )
        if replay:
            (out / "replays").mkdir(exist_ok=True)
            (out / "replays" / f"{r.session_id}.json").write_text(
                json.dumps(replay, indent=2, default=str),
                encoding="utf-8",
            )
    # summary.md written last — a run dir without one is an incomplete artifact.
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def run_batch(
    runner: Runner,
    manifest_id: str,
    personas: list[str],
    *,
    seed: int,
    paraphrase: bool = False,
) -> list[SessionReport]:
    """Run one session per persona entry; batch/seed make the whole set replayable."""
    from .personas import make_persona

    reports: list[SessionReport] = []
    for i, name in enumerate(personas):
        batch_id = f"sim-{seed}-{i}-{uuid_suffix()}"
        runner.seed_batch(manifest_id, batch_id)
        persona = make_persona(name, seed * 1000 + i, paraphrase=paraphrase)
        reports.append(
            runner.run_session(
                manifest_id, batch_id, persona, pid=f"sim-{name}-{i}", seed=seed * 1000 + i
            )
        )
    return reports


def uuid_suffix() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


def render_simulations(out: Path) -> int:
    """Render ``replays/*.json`` into ``simulations/*.html`` via the committed
    node renderer (``frontend/scripts/render_simulation.mjs``) — the same
    ``buildSimulationHtml`` the admin walkthrough's download button runs.

    Returns the number of files written. Node is required; the worker netns
    (--provider ollama) has none, so a miss warns rather than fails the run —
    ``replays/*.json`` can still be rendered later on the host.
    """
    import shutil

    node = shutil.which("node")
    renderer = REPO / "frontend" / "scripts" / "render_simulation.mjs"
    replays = sorted((out / "replays").glob("*.json")) if (out / "replays").is_dir() else []
    if not replays:
        print("  simulations: no replays captured — nothing to render")
        return 0
    if node is None:
        print(
            "  simulations: `node` not on PATH — replay JSON kept under "
            f"{out / 'replays'}; render later with "
            f"node frontend/scripts/render_simulation.mjs {out / 'replays'}/<sid>.json"
        )
        return 0
    sims = out / "simulations"
    sims.mkdir(exist_ok=True)
    written = 0
    for rp in replays:
        dest = sims / f"{rp.stem}.html"
        proc = subprocess.run(
            [node, str(renderer), str(rp), "-o", str(dest)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            written += 1
        else:
            print(f"  simulations: {rp.name} render failed: {proc.stderr.strip()[:200]}")
    return written
