"""Content tooling for WP02 (run from ``backend/``)::

    uv run python -m app.domain.manifests.cli check            # everything; exit 1 on problems
    uv run python -m app.domain.manifests.cli schemas --write  # regenerate content/schemas/*.json
    uv run python -m app.domain.manifests.cli docs --write     # regenerate generated docs
    uv run python -m app.domain.manifests.cli publish --date YYYY-MM-DD   # add new manifests

``publish`` only adds entries for manifests that are not yet published; it refuses to run if a
published manifest or any artifact it references changed (that requires new versions).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from app.domain.manifests.artifacts import ARTIFACT_ADAPTER
from app.domain.manifests.binding import BoundManifest, bind
from app.domain.manifests.diff import (
    ArmDiffError,
    check_declared_diff,
    check_paired_arms,
    load_diff_record,
    profile_diff,
    render_value,
)
from app.domain.manifests.publication import publish, unpublished, verify_publication, write_index
from app.domain.manifests.schema import Manifest
from app.domain.manifests.store import ContentError, ContentStore
from app.timing.conformance import render_markdown

PROFILES = (
    "tp_demo_v1",
    "pe_demo_v1",
    "tp_review_first_v2",
    "pe_pilot_v2",
    # BA-37: re-issued versions binding consent.reference@v2 (literal Appendix B text).
    "tp_demo_v2",
    "pe_demo_v2",
    "tp_review_first_v3",
    "pe_pilot_v3",
    # BA-37 correction: re-issued versions binding consent.reference@v3
    # ('#XXXX-XXXX' — v2 lost the hyphen to an Appendix B line-wrap artifact).
    "tp_demo_v3",
    "pe_demo_v3",
    "tp_review_first_v4",
    "pe_pilot_v4",
    # UX round 3 (plan §17): warm/no-repeat social copy, required own pressure rating,
    # and the FR-1 interaction-mode flag — structured+NL successors plus NL-only siblings.
    "tp_demo_v4",
    "pe_demo_v4",
    "tp_review_first_v5",
    "pe_pilot_v5",
    "tp_demo_nl_v1",
    "pe_demo_nl_v1",
    # O-1/O-2/O-3 (decisions register): re-issued versions binding fallback_bank v3
    # (asks_intel_nl + identity_probe pools) for the resolved broadcast/probe semantics.
    "tp_demo_v5",
    "pe_demo_v5",
    "tp_review_first_v6",
    "pe_pilot_v6",
    "tp_demo_nl_v2",
    "pe_demo_nl_v2",
    # UX round 4 (UX_Feedback_v4, 2026-10-05): re-issued versions binding
    # requests.demo@v2 (per-request template draws), fallback_bank v4
    # (topically distinct social lines), and post_task@v2 (pre-debrief
    # teammate-nature choice item).
    "tp_demo_v6",
    "pe_demo_v6",
    "tp_review_first_v7",
    "pe_pilot_v7",
    "tp_demo_nl_v3",
    "pe_demo_nl_v3",
    # 2026-10-15 crate-condition experiment (locked decisions D1-D10): the
    # overlap_v1 generator + task-shape pins; gated by CRATE_OVERLAP_ENABLED /
    # CRATE_NUMBER_ENABLED for live fielding, always preview-mintable.
    "tp_ai_ovnone_v1",
    "tp_ai_ovsome_v1",
    "tp_ai_ovfull_v1",
    "tp_ai_ovnone_thin_v1",
    "tp_ai_ovsome_thin_v1",
    "tp_ai_ovfull_thin_v1",
    # v2 successors binding training.reference@v7 — the artifact that documents
    # the condition-aware case-background/workspace/connecting copy.
    "tp_ai_ovnone_v2",
    "tp_ai_ovsome_v2",
    "tp_ai_ovfull_v2",
    "tp_ai_ovnone_thin_v2",
    "tp_ai_ovsome_thin_v2",
    "tp_ai_ovfull_thin_v2",
    # Student UX successors, published for previews only. Live bindings stay pinned.
    "tp_ai_v14",
    "tp_ai_ovnone_v3",
    "tp_ai_ovsome_v3",
    "tp_ai_ovfull_v3",
    "tp_ai_ovnone_thin_v3",
    "tp_ai_ovsome_thin_v3",
    "tp_ai_ovfull_thin_v3",
    # v15 (2026-10-17, owner direction): workload relabel + mixed arm —
    # checkin.workload@v1, remarks.tp_workload@v1, post_task@v6, debrief@v4;
    # condition_set high[6,7] + mixed[2,6]. Internal eval only; no live fielding.
    "tp_ai_ovsome_thin_v4",
)
PROFILE_PAIRS = (
    ("tp_demo_v1", "tp_review_first_v2"),
    ("pe_demo_v1", "pe_pilot_v2"),
    ("tp_demo_v2", "tp_review_first_v3"),
    ("pe_demo_v2", "pe_pilot_v3"),
    ("tp_demo_v3", "tp_review_first_v4"),
    ("pe_demo_v3", "pe_pilot_v4"),
    ("tp_demo_v4", "tp_review_first_v5"),
    ("pe_demo_v4", "pe_pilot_v5"),
    ("tp_demo_v5", "tp_review_first_v6"),
    ("pe_demo_v5", "pe_pilot_v6"),
    ("tp_demo_v6", "tp_review_first_v7"),
    ("pe_demo_v6", "pe_pilot_v7"),
)
# Feature-flag siblings (UX round 3, FR-1): same study and arms, differing only in the
# declared interaction mode. Checked like PROFILE_PAIRS but kept apart because they are
# not reference->proposed_pilot successions.
PROFILE_VARIANTS = (
    ("tp_demo_v4", "tp_demo_nl_v1"),
    ("pe_demo_v4", "pe_demo_nl_v1"),
    ("tp_demo_v5", "tp_demo_nl_v2"),
    ("pe_demo_v5", "pe_demo_nl_v2"),
    ("tp_demo_v6", "tp_demo_nl_v3"),
    ("pe_demo_v6", "pe_demo_nl_v3"),
)


def _manifest_schema() -> dict[str, Any]:
    return Manifest.model_json_schema()


def _artifact_schema() -> dict[str, Any]:
    return ARTIFACT_ADAPTER.json_schema()


SCHEMA_FILES: dict[str, Callable[[], dict[str, Any]]] = {
    "manifest.v1.schema.json": _manifest_schema,
    "artifact.v1.schema.json": _artifact_schema,
}
MATRIX_DOC = Path("docs") / "TIMING_CONFORMANCE_MATRIX.md"
DIFF_DOC = Path("docs") / "TIMING_REFERENCE_TO_V2_DIFF.md"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def generated_schemas() -> dict[str, str]:
    return {name: _json(build()) for name, build in SCHEMA_FILES.items()}


def bound_profiles(store: ContentStore) -> list[BoundManifest]:
    return [bind(store, manifest_id, mode="preview") for manifest_id in PROFILES]


def render_diff_doc(store: ContentStore) -> str:
    lines = [
        "# Reference-to-v2 profile differences (INV-0211, checked by INV-0208)",
        "",
        "Generated by `uv run python -m app.domain.manifests.cli docs --write`. Each row is a "
        "difference in the *resolved executable policy* between a reference profile and its "
        "proposed v2 successor. Every row must be declared, with a rationale, in the reviewed "
        "record `content/profiles/diffs/<from>__<to>.json`; `check` fails on any undeclared "
        "or missing difference. The records are `proposed_unapproved` until D01/D07/D10.",
        "",
    ]
    for left_id, right_id in PROFILE_PAIRS + PROFILE_VARIANTS:
        left = bind(store, left_id, mode="preview")
        right = bind(store, right_id, mode="preview")
        record = load_diff_record(store.root, left_id, right_id)
        rationale = {d.path: (d.kind, d.rationale) for d in record.differences}
        lines += [
            f"## `{left_id}` → `{right_id}` ({record.status}; decisions "
            f"{', '.join(record.decisions)})",
            "",
            "| Path | Kind | Reference | v2 | Rationale |",
            "|---|---|---|---|---|",
        ]
        for entry in profile_diff(left, right):
            kind, why = rationale.get(entry.path, ("UNDECLARED", "UNDECLARED"))
            lines.append(
                f"| `{entry.path}` | {kind} | `{render_value(entry.left)}` | "
                f"`{render_value(entry.right)}` | {why} |".replace("\n", " ")
            )
        lines.append("")
    return "\n".join(lines)


def generated_docs(store: ContentStore) -> dict[Path, str]:
    return {
        MATRIX_DOC: render_markdown(bound_profiles(store)),
        DIFF_DOC: render_diff_doc(store),
    }


def _stale(root: Path, files: dict[Path, str]) -> Iterator[str]:
    for rel, text in files.items():
        path = root / rel
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            yield f"{rel.as_posix()} is stale; regenerate it"


def run_check(root: Path) -> list[str]:
    problems: list[str] = []
    try:
        store = ContentStore(root / "content")
    except ContentError as exc:
        return exc.problems
    problems += verify_publication(store)
    problems += [f"{m}: not published" for m in unpublished(store)]
    missing = [p for p in PROFILES if p not in store.manifests]
    problems += [f"required profile {p} is missing" for p in missing]
    if problems:
        return problems
    for manifest_id in PROFILES:
        try:
            bound = bind(store, manifest_id, mode="preview")
            check_paired_arms(bound)
        except (ContentError, ArmDiffError) as exc:
            problems.append(str(exc))
    for left_id, right_id in PROFILE_PAIRS + PROFILE_VARIANTS:
        record = load_diff_record(store.root, left_id, right_id)
        actual = profile_diff(
            bind(store, left_id, mode="preview"), bind(store, right_id, mode="preview")
        )
        problems += [f"{left_id}->{right_id}: {p}" for p in check_declared_diff(record, actual)]
    schema_files = {Path("content") / "schemas" / n: t for n, t in generated_schemas().items()}
    problems += list(_stale(root, schema_files))
    problems += list(_stale(root, generated_docs(store)))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Investigators content tooling (WP02)")
    parser.add_argument("--root", type=Path, default=None, help="repository root")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    for name in ("schemas", "docs"):
        sp = sub.add_parser(name)
        sp.add_argument("--write", action="store_true")
    pub = sub.add_parser("publish")
    pub.add_argument("--date", required=True)
    args = parser.parse_args(argv)
    root: Path = args.root or repo_root()

    if args.command == "check":
        problems = run_check(root)
        for problem in problems:
            print(f"FAIL: {problem}")
        if not problems:
            print(f"OK: {len(PROFILES)} profiles x 2 arms bind; publication, diffs, docs current")
        return 1 if problems else 0
    if args.command == "schemas":
        files = {Path("content") / "schemas" / n: t for n, t in generated_schemas().items()}
    elif args.command == "docs":
        files = generated_docs(ContentStore(root / "content"))
    else:
        store = ContentStore(root / "content")
        index, added = publish(store, published_on=args.date)
        write_index(root / "content", index)
        print(f"published: {added or 'nothing new'}")
        return 0
    stale = list(_stale(root, files))
    if args.write:
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        print(f"wrote {len(files)} file(s)")
        return 0
    for line in stale:
        print(f"FAIL: {line}")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
