#!/usr/bin/env python3
"""World-generator validation + invariant-7 report (INV-0509/0510; spec §7.1).

Runs the §7.1 validator over N seeded worlds (default 1000) and prints a JSON report:
per-invariant failure counts plus the joint distribution of
``rating seat x decisive-record holder x scripted-request sender`` that feeds D02 and
the admin balance report (INV-1302).

Sender-dimension independence (BA-10): the scripted-request sender multiset is a
documented fixed balanced design (2 x JT, 1 x PM per session) — sender counts are
*not* expected to be uniform. What must hold is that the sender draw is independent
of the rating-seat and decisive-holder assignments: the report carries a Pearson
chi-square independence assertion (alpha = 0.001) for sender x rating-seat and
sender x decisive-holder. A structural dependence would need a D02 citation; the
exit code is 1 on any invariant violation *or* a failed independence assertion.

    uv run ../scripts/validate_worlds.py [--seeds N]
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domain.manifests.binding import bind  # noqa: E402
from app.domain.manifests.store import ContentStore  # noqa: E402
from app.domain.rng import RandomStreams  # noqa: E402
from app.domain.schedules import plan_rating_seats, plan_scripted_requests  # noqa: E402
from app.domain.world import build_world, validate_world, with_teammate_assignments  # noqa: E402
from app.timing.policy import resolve_policy  # noqa: E402

# Chi-square critical values (no scipy dependency): df=2 at alpha=0.001 — strict so the
# independence gate does not flake on ordinary sampling noise over many worlds.
CHI2_DF = 2
CHI2_ALPHA = 0.001
CHI2_CRITICAL = 13.816


def chi2_statistic(table: list[list[int]]) -> float:
    """Pearson chi-square independence statistic for a small contingency table."""
    n = sum(sum(row) for row in table)
    if n == 0:
        return 0.0
    row_totals = [sum(row) for row in table]
    col_totals = [sum(table[r][c] for r in range(len(table))) for c in range(len(table[0]))]
    stat = 0.0
    for r, row in enumerate(table):
        for c, observed in enumerate(row):
            expected = row_totals[r] * col_totals[c] / n
            if expected > 0:
                stat += (observed - expected) ** 2 / expected
    return stat


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=1000)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    store = ContentStore.load_default()
    bound = bind(store, "tp_demo_v1", mode="qa")
    policy = resolve_policy(bound, "high")  # arm is irrelevant to world/schedule draws

    failures: Counter[str] = Counter()
    joint: Counter[str] = Counter()
    holders: Counter[str] = Counter()
    senders: Counter[str] = Counter()
    # BA-10: 2x3 contingency cells for the independence assertions — sender (JT/PM/none)
    # crossed with rating seat and with decisive-record holder.
    cells_rs: Counter[tuple[str, str]] = Counter()  # (rating_seat, sender)
    cells_hs: Counter[tuple[str, str]] = Counter()  # (holder, sender)
    sender_multiset: Counter[str] = Counter()

    for i in range(args.seeds):
        seed = secrets.token_hex(16) + f"{i:08x}"
        streams = RandomStreams(seed)
        world = build_world(streams)
        requests = plan_scripted_requests(
            policy,
            streams,
            assigned=world.assigned,
            unassigned=[n for n in range(1, 9) if n not in world.assigned],
        )
        world = with_teammate_assignments(
            streams, world, [(p.seat, p.crate) for p in requests]
        )
        for problem in validate_world(world):
            failures[problem.split(":")[0]] += 1

        rating = plan_rating_seats(policy, streams).higher_seat
        sender_of: dict[int, str] = {p.crate: p.seat for p in requests}
        sender_multiset.update(p.seat for p in requests)
        for crate, rid in world.participant_decisive.items():
            holder = next(s for s in ("JT", "PM") if rid in world.holdings[s])
            send = sender_of.get(crate, "none")
            holders[holder] += 1
            senders[send] += 1
            joint[f"{holder}|{rating}|{send}"] += 1
            cells_rs[(rating, send)] += 1
            cells_hs[(holder, send)] += 1

    seats = ("JT", "PM")
    cols = ("JT", "PM", "none")
    table_rs = [[cells_rs[(r, c)] for c in cols] for r in seats]
    table_hs = [[cells_hs[(r, c)] for c in cols] for r in seats]
    stat_rs = chi2_statistic(table_rs)
    stat_hs = chi2_statistic(table_hs)
    independent = stat_rs <= CHI2_CRITICAL and stat_hs <= CHI2_CRITICAL

    total_decisive = sum(holders.values())
    report = {
        "seeds": args.seeds,
        "profile": "tp_demo_v1",
        "invariant_failures": dict(failures),
        "decisive_records": total_decisive,
        "holder_distribution": dict(holders),
        "sender_distribution": dict(senders),
        "joint_holder_rating_sender": dict(joint),
        # BA-10: the sender multiset is a fixed balanced design, not a uniform draw —
        # independence from rating-seat/holder assignment is the property under test.
        "sender_independence": {
            "sender_design": {
                "policy": "shuffle_fixed_multiset",
                "multiset": dict(sorted(sender_multiset.items())),
                "note": (
                    "fixed scripted-request sender multiset is a documented balanced "
                    "design at the schedule level; sender counts are not expected to be "
                    "uniform. The chi-square assertion checks independence of the "
                    "sender draw from rating-seat and decisive-holder assignment. A "
                    "structural dependence requires a D02 citation."
                ),
            },
            "assertions": {
                "sender_x_rating_seat": {
                    "table": table_rs,
                    "chi2": stat_rs,
                    "pass": stat_rs <= CHI2_CRITICAL,
                },
                "sender_x_decisive_holder": {
                    "table": table_hs,
                    "chi2": stat_hs,
                    "pass": stat_hs <= CHI2_CRITICAL,
                },
            },
            "df": CHI2_DF,
            "alpha": CHI2_ALPHA,
            "critical_value": CHI2_CRITICAL,
            "pass": independent,
        },
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 1 if failures or not independent else 0


if __name__ == "__main__":
    raise SystemExit(main())
