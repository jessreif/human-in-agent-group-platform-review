"""Oracle layer: always-on invariants + per-persona scenario expectations.

Invariants run against the durable record after every session — they encode the
properties a correct implementation must hold *regardless* of what the bot did
(no unprompted shares, no near-repeat deliveries, correct seat attribution, the
answer-key bound on assistant output, valid episode/receipt bookkeeping).

Scenario expectations come from ``persona.expectations`` tokens and encode what a
session of that persona should produce (an idler must die `checkin_incomplete`; a
sharer's scripted-ask episodes must close `participant_share`). A violated
expectation means the scenario didn't do its job — distinct from an invariant
failure, which means the implementation misbehaved.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.db import schema as s
from app.domain.world import HIDEOUTS
from app.services import engines

EXPECTED_REJECTIONS = {
    "stale_revision",
    "wrong_stage",
    "session_terminal",
    "unknown_command",
    "writer_conflict",
    "malformed_request",
    "malformed_reply",
    "malformed_share",
    "malformed_item",
    "malformed_answer",
    "malformed_consent",
    "malformed_initials",
    "malformed_choice",
    "channel_closed",
    "over_cap",
    "no_teammate_records",
    "record_not_in_file",
    "unknown_item",
    "instrument_unavailable",
    "instrument_incomplete",
    "no_open_checkin",
    "pressure_out_of_range",
    "skip_not_permitted",
    "task_closed",
    "confidence_needs_crate",
    "confidence_out_of_range",
    "confidence_not_applicable",
    "no_unique_next_stage",
    "illegal_stage",
    "choice_locked",
    "initials_locked",
    "structured_request_disabled",
    "writer_lease_held",
    "arm_not_open",
}

CRATE_MENTION = re.compile(r"\bcrate\s*#?\s*\d\b", re.I)

# An ask accepted this close to the budget can never be answered: the
# structured_record delay clamps at 26.4 s plus a ≤9 s typing component. A
# `deadline` close inside the window is honest participant behavior, not a
# failure (the personas keep asking — real participants will too).
LATE_ASK_DEAD_WINDOW_MS = 36_000

# Oracle contract version — bump whenever an invariant or expectation's
# semantics change so a run's green is attributable to a specific rule set
# (stamped into summary.md alongside the source bundle hash).
ORACLE_VERSION = "oracles_v3"


def _budget_ms(bound: Any, arm: str | None) -> int:
    """The task active-budget for the session's arm (falls back to arm 0 / 360 s)."""
    from app.timing.policy import PolicyError, resolve_policy

    try:
        return int(resolve_policy(bound, arm or bound.manifest.arms[0]).budget_ms)
    except (PolicyError, IndexError):
        return 360_000


@dataclass(frozen=True)
class Finding:
    check: str  # invariant id or expectation exp
    severity: str  # "info" | "warn" | "fail"
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _delivered_texts(
    engine: Engine, sid: uuid.UUID, *, exclude_cards: bool = False
) -> list[tuple[str, str, str]]:
    """(channel, actor, body) for every committed teammate/assistant message.

    ``exclude_cards`` drops record-share lines (``record_id`` set): those are
    intentionally verbatim card text — the same record re-shared, or the shared
    "Sharing intel on Crate N" template — and are not what near-repeat gating
    exists to catch."""
    with engine.connect() as conn:
        q = select(s.message.c.channel, s.message.c.actor, s.message.c.body).where(
            s.message.c.session_id == sid,
            s.message.c.committed_seq.is_not(None),
            s.message.c.actor.in_(("JT", "PM", "assistant")),
        )
        if exclude_cards:
            q = q.where(s.message.c.record_id.is_(None))
        return [
            (r.channel, r.actor, r.body)
            for r in conn.execute(q.order_by(s.message.c.committed_seq))
        ]


def check_session(
    engine: Engine, sid: uuid.UUID, persona_name: str, bound: Any = None
) -> list[Finding]:
    """Run every always-on invariant plus the persona's scenario expectations."""
    verbatim = _verbatim_lines(bound)
    findings: list[Finding] = []
    findings.extend(_invariants(engine, sid, verbatim))
    findings.extend(_expectations(engine, sid, persona_name, bound))
    return findings


def _verbatim_lines(bound: Any) -> frozenset[str]:
    """Canonical scripted lines from the pinned fallback bank (identity_probe
    disclosures, no-holdings lines, stalls…) — verbatim by design, so they are
    exempt from the near-repeat gate but still counted for conspicuous-repetition
    warnings."""
    if bound is None:
        return frozenset()
    stored = bound.artifacts.get("teammates.fallback_bank")
    if stored is None:
        return frozenset()
    lines: set[str] = set()
    for rule in getattr(stored.artifact.body, "rules", ()):
        for line in getattr(rule, "lines", ()):
            lines.add(line.strip().lower())
    return frozenset(lines)


# ---------------------------------------------------------------------------
# Always-on invariants
# ---------------------------------------------------------------------------


def _invariants(engine: Engine, sid: uuid.UUID, verbatim: frozenset[str]) -> list[Finding]:
    out: list[Finding] = []
    out.extend(_inv_unprompted_shares(engine, sid))
    out.extend(_inv_near_repeat(engine, sid, verbatim))
    out.extend(_inv_scripted_repeat(engine, sid, verbatim))
    out.extend(_inv_probe_disclosure(engine, sid))
    out.extend(_inv_seat_attribution(engine, sid))
    out.extend(_inv_assistant_bounded(engine, sid))
    out.extend(_inv_assistant_repeat(engine, sid))
    out.extend(_inv_episode_bookkeeping(engine, sid))
    out.extend(_inv_receipt_reasons(engine, sid))
    return out


def _inv_assistant_repeat(engine: Engine, sid: uuid.UUID) -> list[Finding]:
    """INV-ASSISTANT-REPEAT: the assistant redelivering one body ≥3 times is a
    documentation-level warning — the AB-15 redirect dead-end and deterministic
    summary resends show up here without conflating them with the teammate
    believability gate."""
    bodies = [
        b.strip()
        for ch, a, b in _delivered_texts(engine, sid)
        if ch == "assistant" and a == "assistant" and b
    ]
    findings = []
    seen: set[str] = set()
    for body in bodies:
        n = bodies.count(body)
        if n >= 3 and body not in seen:
            seen.add(body)
            findings.append(
                Finding(
                    "INV-ASSISTANT-REPEAT",
                    "warn",
                    f"assistant delivered the same line {n}x",
                    {"body": body[:160]},
                )
            )
    return findings


def _inv_unprompted_shares(engine: Engine, sid: uuid.UUID) -> list[Finding]:
    """INV-SHARE: teammate shares must trace to a request episode (AB-03)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(s.share.c.share_id, s.share.c.sharer, s.share.c.source).where(
                s.share.c.session_id == sid,
                s.share.c.sharer.in_(("JT", "PM")),
                s.share.c.prompted_by_request_id.is_(None),
            )
        ).all()
    if not rows:
        return []
    return [
        Finding(
            "INV-UNPROMPTED-SHARE",
            "fail",
            f"{len(rows)} teammate share(s) with no prompting request",
            {"shares": [str(r.share_id) for r in rows], "sources": [r.source for r in rows]},
        )
    ]


def _norm(body: str) -> str:
    return body.strip().lower()


def _inv_near_repeat(engine: Engine, sid: uuid.UUID, verbatim: frozenset[str]) -> list[Finding]:
    """INV-REPEAT: no two delivered *teammate* lines may be near-paraphrases.

    Scoped to JT/PM — the gate is a teammate-believability control; the assistant
    is a declared bot (its repeat patterns are tracked separately in
    INV-ASSISTANT-REPEAT as a documentation-level warning). Share cards and
    canonical scripted (verbatim) lines are excluded — they are supposed to
    repeat; conspicuous scripted repetition is reported by INV-SCRIPTED-REPEAT.
    Cross-seat near-repeats are fails (AB-16: the seats look copy-pasted);
    same-seat repeats are warns — repetitive, but a human re-answering the same
    question may legitimately echo themselves. Findings are deduped per body."""
    texts = [
        (ch, a, b)
        for ch, a, b in _delivered_texts(engine, sid, exclude_cards=True)
        if a in ("JT", "PM") and _norm(b) not in verbatim
    ]
    return _near_repeat_findings(texts)


def _near_repeat_findings(texts: list[tuple[str, str, str]]) -> list[Finding]:
    """Pure core of INV-NEAR-REPEAT — ``texts`` is (channel, actor, body) for
    delivered teammate lines already filtered of cards/verbatim scripted lines.
    Kept separate so the register's archived failing transcripts can pin the
    severity contract as permanent fixtures (see tests/test_agent_sim_harness.py)."""
    bodies = [b for _, _, b in texts]
    findings: dict[str, Finding] = {}
    for i, (_, actor_a, body_a) in enumerate(texts):
        for j, (_, actor_b, body_b) in enumerate(texts):
            if i >= j or not body_a or not body_b:
                continue
            if not engines.is_near_repeat(body_a, [body_b]):
                continue
            key = body_a.strip().lower() if len(body_a) <= len(body_b) else body_b.strip().lower()
            severity = "warn" if actor_a == actor_b else "fail"
            existing = findings.get(key)
            if existing is not None and existing.severity == "fail":
                continue
            findings[key] = Finding(
                "INV-NEAR-REPEAT",
                severity,
                f"delivered near-repeat ({actor_a} vs {actor_b}; "
                f"{bodies.count(body_a) + bodies.count(body_b)} similar lines)",
                {"a": body_a[:120], "b": body_b[:120]},
            )
    return list(findings.values())


def _inv_scripted_repeat(engine: Engine, sid: uuid.UUID, verbatim: frozenset[str]) -> list[Finding]:
    """INV-SCRIPTED-REPEAT: a canonical scripted line delivered ≥3 times is
    conspicuous even though it is verbatim by design (e.g. six identical
    disclosure lines under repeated probing) — warn, don't fail."""
    if not verbatim:
        return []
    bodies = [
        _norm(b)
        for _, a, b in _delivered_texts(engine, sid, exclude_cards=True)
        if a in ("JT", "PM") and _norm(b) in verbatim
    ]
    findings = []
    for body in set(bodies):
        n = bodies.count(body)
        if n >= 3:
            findings.append(
                Finding(
                    "INV-SCRIPTED-REPEAT",
                    "warn",
                    f"canonical scripted line delivered {n}x",
                    {"body": body[:160]},
                )
            )
    return findings


def _inv_probe_disclosure(engine: Engine, sid: uuid.UUID) -> list[Finding]:
    """INV-PROBE: *each* participant probe message must have its own
    ``automation_probe`` event (matched by ``payload.message_id``) — aggregate
    counts can mask an unaudited probe (BA-82)."""
    with engine.connect() as conn:
        probe_msgs = {
            str(r.message_id): r.body
            for r in conn.execute(
                select(s.message.c.message_id, s.message.c.body).where(
                    s.message.c.session_id == sid,
                    s.message.c.channel == "team",
                    s.message.c.actor == "participant",
                )
            )
            if r.body and engines.is_automation_probe(r.body)
        }
        evented = {
            mid
            for (mid,) in conn.execute(
                select(s.event.c.payload["message_id"].as_string()).where(
                    s.event.c.session_id == sid,
                    s.event.c.type == "automation_probe",
                )
            )
            if mid
        }
    missing = {mid: body for mid, body in probe_msgs.items() if mid not in evented}
    if missing:
        return [
            Finding(
                "INV-PROBE-DISCLOSURE",
                "fail",
                f"{len(missing)} of {len(probe_msgs)} probe message(s) lack an "
                f"automation_probe event",
                {"probe_bodies": list(missing.values())},
            )
        ]
    return []


def _inv_seat_attribution(engine: Engine, sid: uuid.UUID) -> list[Finding]:
    """INV-SEAT: a structured ask's responder must hold the newest record for the crate."""
    with engine.connect() as conn:
        asks = conn.execute(
            select(s.request.c.request_id, s.request.c.crate, s.request.c.responder).where(
                s.request.c.session_id == sid,
                s.request.c.requester == "participant",
                s.request.c.responder.in_(("JT", "PM")),
                s.request.c.kind == "structured",
            )
        ).all()
        content = conn.execute(
            select(s.world_snapshot.c.content).where(s.world_snapshot.c.session_id == sid)
        ).scalar_one_or_none()
        if content is None:
            return []
        holdings = {seat: set(recs) for seat, recs in (content.get("holdings") or {}).items()}
        ordinals = {
            (str(r.record_id)): (r.crate, r.timeline_ordinal)
            for r in conn.execute(
                select(s.record.c.record_id, s.record.c.crate, s.record.c.timeline_ordinal).where(
                    s.record.c.session_id == sid
                )
            )
        }
    findings = []
    for ask in asks:
        best_seat, best_ord = None, -1
        for seat in ("JT", "PM"):
            for rid in holdings.get(seat, ()):
                rec = ordinals.get(rid)
                if rec and rec[0] == ask.crate and rec[1] > best_ord:
                    best_seat, best_ord = seat, rec[1]
        if best_seat is not None and best_seat != ask.responder:
            findings.append(
                Finding(
                    "INV-SEAT-ATTRIBUTION",
                    "fail",
                    f"crate {ask.crate} routed to {ask.responder} but {best_seat} holds the newest",
                    {"request_id": str(ask.request_id)},
                )
            )
    return findings


def _inv_assistant_bounded(engine: Engine, sid: uuid.UUID) -> list[Finding]:
    """INV-ANSWERKEY: two bounds on delivered assistant text.

    1. No line may dump the answer key — multiple crates named *alongside* a
       location. SQ-8: the crate→item map is a shipped feature, so a contents
       answer naming several crates with no location is legitimate.
    2. *Model-originated* text (``displayed_output.fallback = 'none'``) must
       still obey the app's own ``answer_key_like`` gate — >1 crate named is
       rejectable validator output; if it delivered, the gate contract failed.
       The sanctioned deterministic route (``deterministic_contents_summary``)
       is exempt — it is not model text and never reaches the validator."""
    findings = []
    for channel, actor, body in _delivered_texts(engine, sid):
        if channel != "assistant" or actor != "assistant" or not body:
            continue
        if _location_dump(body):
            findings.append(
                Finding(
                    "INV-ASSISTANT-BOUNDED",
                    "fail",
                    "delivered assistant line names multiple crates with locations",
                    {"body": body[:200], "crates": _crates_in(body)},
                )
            )
    with engine.connect() as conn:
        rows = conn.execute(
            select(s.displayed_output.c.shown_text, s.displayed_output.c.fallback)
            .select_from(
                s.displayed_output.join(
                    s.message,
                    (s.message.c.session_id == s.displayed_output.c.session_id)
                    & (s.message.c.message_id == s.displayed_output.c.message_id),
                )
            )
            .where(
                s.displayed_output.c.session_id == sid,
                s.message.c.channel == "assistant",
                s.message.c.actor == "assistant",
            )
        ).all()
    for shown, fallback in rows:
        if _model_gate_breach(shown, fallback):
            findings.append(
                Finding(
                    "INV-ASSISTANT-BOUNDED",
                    "fail",
                    f"assistant text outside the contents route names "
                    f"{len(_crates_in(shown))} crates (fallback={fallback!r}) — "
                    "answer_key_like should have rejected it",
                    {"body": shown[:200], "crates": _crates_in(shown), "fallback": fallback},
                )
            )
    return findings


def _crates_in(body: str) -> list[int]:
    return sorted({int(m.group(0)[-1]) for m in CRATE_MENTION.finditer(body)})


def _location_dump(body: str) -> bool:
    """Multi-crate + a named hideout — the answer-key leak both docs pin."""
    return len(_crates_in(body)) > 1 and any(loc.lower() in body.lower() for loc in HIDEOUTS)


def _model_gate_breach(shown: str, fallback: str) -> bool:
    """Model-originated assistant text obeying the app's own ``answer_key_like``
    bound (>1 crate). The sanctioned deterministic contents route is exempt —
    it is not model text and never reaches the validator."""
    if not shown or fallback == "deterministic_contents_summary":
        return False
    return len(_crates_in(shown)) > 1


def _inv_episode_bookkeeping(engine: Engine, sid: uuid.UUID) -> list[Finding]:
    """INV-EPISODE: closed episodes carry known close reasons; terminal sessions leave
    no silently-pending request episodes."""
    with engine.connect() as conn:
        closed = conn.execute(
            select(s.request_episode.c.close_reason, func.count())
            .where(
                s.request_episode.c.session_id == sid,
                s.request_episode.c.closed_at.is_not(None),
            )
            .group_by(s.request_episode.c.close_reason)
        ).all()
        pending = conn.execute(
            select(func.count())
            .select_from(s.request_episode)
            .where(
                s.request_episode.c.session_id == sid,
                s.request_episode.c.closed_at.is_(None),
            )
        ).scalar_one()
        stage: str = conn.execute(
            select(s.session.c.stage).where(s.session.c.session_id == sid)
        ).scalar_one()
    findings = []
    known = {"delivered", "deadline", "participant_share", "session_abandoned", "no_response"}
    for reason, n in closed:
        if reason not in known:
            findings.append(
                Finding(
                    "INV-EPISODE-BOOKKEEPING",
                    "warn",
                    f"{n} episode(s) closed with unknown reason {reason!r}",
                    {},
                )
            )
    if (
        stage
        in (
            "completion",
            "withdrawn",
            "excluded",
            "abandoned",
            "technical_stop",
        )
        and pending
    ):
        findings.append(
            Finding(
                "INV-EPISODE-BOOKKEEPING",
                "warn",
                f"{pending} request episode(s) still pending at terminal stage {stage}",
                {},
            )
        )
    return findings


def _inv_receipt_reasons(engine: Engine, sid: uuid.UUID) -> list[Finding]:
    """INV-RECEIPT: rejected commands must carry known rejection reasons."""
    with engine.connect() as conn:
        bad = conn.execute(
            select(s.command_receipt.c.kind, s.command_receipt.c.reason).where(
                s.command_receipt.c.session_id == sid,
                s.command_receipt.c.outcome != "accepted",
            )
        ).all()
    findings = []
    for kind, reason in bad:
        if reason not in EXPECTED_REJECTIONS:
            findings.append(
                Finding(
                    "INV-RECEIPT-REASON",
                    "warn",
                    f"command {kind} rejected with unexpected reason {reason!r}",
                    {"reason": reason},
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Scenario expectations (persona-declared tokens)
# ---------------------------------------------------------------------------


def _expectations(
    engine: Engine, sid: uuid.UUID, persona_name: str, bound: Any = None
) -> list[Finding]:
    from .personas import REGISTRY

    tokens = getattr(REGISTRY.get(persona_name, object), "expectations", ())
    if not tokens:
        return []
    with engine.connect() as conn:
        row = conn.execute(
            select(s.session.c.stage, s.session.c.terminal_reason).where(
                s.session.c.session_id == sid
            )
        ).one()
        stage, terminal_reason = row
        episodes = {
            r.close_reason: r.n
            for r in conn.execute(
                select(s.request_episode.c.close_reason, func.count().label("n"))
                .where(s.request_episode.c.session_id == sid)
                .group_by(s.request_episode.c.close_reason)
            )
        }
        pending = conn.execute(
            select(func.count())
            .select_from(s.request_episode)
            .where(
                s.request_episode.c.session_id == sid,
                s.request_episode.c.closed_at.is_(None),
            )
        ).scalar_one()
        # BA-83: correlate each structured ask with *its own* episode outcome —
        # aggregate delivered totals can be filled by unrelated episodes.
        structured_reqs = {
            r.request_id: r.accepted_active_ms
            for r in conn.execute(
                select(s.request.c.request_id, s.request.c.accepted_active_ms).where(
                    s.request.c.session_id == sid,
                    s.request.c.requester == "participant",
                    s.request.c.responder.in_(("JT", "PM")),
                    s.request.c.kind == "structured",
                )
            )
        }
        arm = conn.execute(
            select(s.session.c.arm).where(s.session.c.session_id == sid)
        ).scalar_one_or_none()
        episode_close = {
            r.request_id: r.close_reason
            for r in conn.execute(
                select(s.request_episode.c.request_id, s.request_episode.c.close_reason).where(
                    s.request_episode.c.session_id == sid
                )
            )
        }
        # BA-82: per-message probe/event matching (payload.message_id), not counts.
        probe_msgs = {
            str(r.message_id)
            for r in conn.execute(
                select(s.message.c.message_id, s.message.c.body).where(
                    s.message.c.session_id == sid,
                    s.message.c.channel == "team",
                    s.message.c.actor == "participant",
                )
            )
            if r.body and engines.is_automation_probe(r.body)
        }
        probe_events = {
            mid
            for (mid,) in conn.execute(
                select(s.event.c.payload["message_id"].as_string()).where(
                    s.event.c.session_id == sid, s.event.c.type == "automation_probe"
                )
            )
            if mid
        }
        resume_receipts = conn.execute(
            select(func.count())
            .select_from(s.command_receipt)
            .where(
                s.command_receipt.c.session_id == sid,
                s.command_receipt.c.kind == "resume",
                s.command_receipt.c.outcome == "accepted",
            )
        ).scalar_one()

    findings: list[Finding] = []

    def expect(
        ok: bool, token_name: str, detail: str, evidence: dict[str, Any] | None = None
    ) -> None:
        findings.append(
            Finding(
                f"EXPECT:{token_name}",
                "info" if ok else "fail",
                detail,
                evidence or {},
            )
        )

    for exp in tokens:
        if exp.startswith("terminal:"):
            want = exp.split(":", 1)[1]
            expect(
                stage == want,
                exp,
                f"terminal stage {stage!r} (wanted {want!r})",
            )
        elif exp.startswith("terminal_reason:"):
            want = exp.split(":", 1)[1]
            expect(
                terminal_reason == want,
                exp,
                f"terminal reason {terminal_reason!r} (wanted {want!r})",
            )
        elif exp == "episodes:participant_share":
            expect(
                episodes.get("participant_share", 0) > 0,
                exp,
                f"episodes closed via participant_share: {episodes.get('participant_share', 0)}",
            )
        elif exp == "episodes:no_orphaned":
            expect(
                pending == 0,
                exp,
                f"{pending} request episodes still pending",
            )
        elif exp == "structured_response:each_ask":
            # BA-83: every structured ask must close `delivered` on its own
            # episode; and a structured-capable profile with zero asks means the
            # persona never exercised the path — a scenario failure, not a pass.
            # A `deadline` close is legitimate only inside the dead window: the
            # structured_record delay clamps at 26.4 s plus ≤9 s typing, so an
            # ask accepted in the last ~36 s of the budget can land after the
            # freeze no matter what the draw is. Personas deliberately keep
            # asking in that window (real participants will too).
            structured_allowed = bound is None or bound.manifest.interaction_mode != "nl_only"
            budget_ms = _budget_ms(bound, arm)
            late = {
                rid
                for rid, at_ms in structured_reqs.items()
                if at_ms is not None and at_ms >= budget_ms - LATE_ASK_DEAD_WINDOW_MS
            }
            undelivered = [
                str(rid)
                for rid in structured_reqs
                if episode_close.get(rid) != "delivered"
                and not (rid in late and episode_close.get(rid) == "deadline")
            ]
            expired_late = [str(rid) for rid in late if episode_close.get(rid) == "deadline"]
            if not structured_reqs:
                if structured_allowed:
                    expect(
                        False,
                        exp,
                        "no structured asks issued on a structured-capable profile "
                        "(scenario never exercised the path)",
                    )
                else:
                    expect(True, exp, "not applicable — nl_only profile refuses structured asks")
            else:
                expect(
                    not undelivered,
                    exp,
                    f"{len(structured_reqs) - len(undelivered)}/{len(structured_reqs)} "
                    f"structured ask(s) closed delivered"
                    + (
                        f" ({len(expired_late)} expired inside the dead window)"
                        if expired_late
                        else ""
                    ),
                    {"undelivered": undelivered, "dead_window_expired": expired_late},
                )
        elif exp == "probes:all_disclosed":
            # BA-82: each probe message needs its own automation_probe event.
            undisclosed = sorted(probe_msgs - probe_events)
            expect(
                bool(probe_msgs) and not undisclosed,
                exp,
                f"{len(probe_msgs)} probe message(s), "
                f"{len(probe_msgs) - len(undisclosed)} disclosed",
                {"undisclosed_message_ids": undisclosed},
            )
        elif exp == "resume:accepted":
            # A same-writer resume is a silent lease renew (no 'resumed' event —
            # that event marks an actual writer change); the durable evidence of
            # a successful reconnect is the accepted command receipt.
            expect(resume_receipts > 0, exp, f"{resume_receipts} accepted resume receipt(s)")
        elif exp == "assistant:no_crash":
            expect(stage != "aborted", exp, f"stage {stage!r}")
        elif exp == "no_crash":
            expect(
                stage in ("completion", "technical_stop", "withdrawn", "excluded", "abandoned"),
                exp,
                f"reached terminal stage {stage!r}",
            )
        elif exp == "integrity:only_expected_rejections":
            expect(True, exp, "checked via INV-RECEIPT-REASON findings")
    return findings
