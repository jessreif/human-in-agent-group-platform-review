"""Session runner: enroll, drive the stage spine, and run the bot loop.

The loop models two clocks the way the real deployment does:

* **active task time** — advanced deterministically by ``set_active`` on the open
  running interval each task iteration (the check-in pauses it, so active jobs
  like the deadline/check-in stop accruing while the overlay is up);
* **wall time** — accumulated as ``wall_credit`` per iteration; wall-basis jobs
  become due when the credit reaches their scheduled delay. This is what lets an
  idling participant hit the 120 s check-in cap *while still in the check-in* —
  and lets a responsive one return to task before it fires.

Each iteration: credit wall → force due wall jobs → advance active → tick worker
→ snapshot view → persona decides → dispatch actions → tick again. Every command
outcome is appended to the transcript.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import insert, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db import schema as s
from app.db.tx import Command, execute_command
from app.domain.manifests.binding import BoundManifest, bind
from app.domain.manifests.store import ContentStore
from app.flags import FeatureFlags
from app.services import lifecycle
from app.services.entry import EntryResult, enter
from app.services.sessions import register_manifest

from . import clockwork
from .oracles import Finding, check_session
from .personas import Action, Persona
from .view import participant_view

BUILD_SHA = "ab" * 32
TERMINAL = {"completion", "withdrawn", "excluded", "abandoned", "technical_stop"}


def _expect_reject_finding(action: Action, outcome: Any, stage: str) -> Finding | None:
    """BA-81: a hostile action accepted by the command path is a fail finding."""
    if not action.expect_reject or not outcome.accepted:
        return None
    return Finding(
        "INV-EXPECT-REJECT",
        "fail",
        f"{action.note or action.kind}: hostile command was accepted",
        {"kind": action.kind, "payload": action.payload, "note": action.note, "stage": stage},
    )


@dataclass
class Step:
    n: int
    stage: str
    active_ms: int
    wall_ms: int
    actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SessionReport:
    session_id: str
    persona: str
    seed: int
    manifest_id: str
    arm: str | None
    stage: str
    terminal_reason: str | None
    active_ms: int
    steps: list[Step] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    # Admin-replay payload captured while the sim DB is still alive — the same
    # shape the admin walkthrough/simulation consumes, so a bot session renders
    # through the identical artifact as a live one (researcher tier).
    replay: dict[str, Any] | None = None

    @property
    def failed(self) -> int:
        return sum(1 for f in self.findings if f.severity == "fail")


class Runner:
    """Drives one-or-many simulated sessions against a SimDatabase."""

    def __init__(
        self,
        db: Any,  # SimDatabase (engine bundle)
        store: ContentStore,
        *,
        provider: Any = None,
        worker: str = "sim-worker",
        wall_step_ms: int = 4_000,
        max_steps: int = 400,
        time_pressure: bool = True,
        ai_qa: bool = False,
        bonus_payment: bool = True,
    ) -> None:
        self.db = db
        self.store = store
        self.provider = provider
        self.worker = worker
        self.wall_step_ms = wall_step_ms
        self.max_steps = max_steps
        # TIME_PRESSURE_ENABLED mirror: sims exercise the deployment flag —
        # handler_for / advance_due / tick all receive the same value the
        # API+worker would read from settings.
        self.time_pressure = time_pressure
        # AI_QA_ENABLED mirror: flag-on sessions route training completion
        # through the ai_qa stage and accept the qa_ask command.
        self.ai_qa = ai_qa
        # BONUS_PAYMENT_ENABLED mirror: flag-off sessions consent against the
        # nobonus artifact and entitle under the no-bonus payment policy.
        self.bonus_payment = bonus_payment

    # -- enrollment ---------------------------------------------------------

    def seed_batch(self, manifest_id: str, batch_id: str) -> BoundManifest:
        bound = bind(self.store, manifest_id, mode="qa")
        with self.db.superuser.begin() as conn:
            register_manifest(conn, bound)
            conn.execute(
                insert(s.batch).values(
                    batch_id=batch_id,
                    study_key=bound.manifest.study_key,
                    manifest_id=bound.manifest_id,
                    mode="qa",
                    enrollment_open=True,
                )
            )
        return bound

    def enroll(self, batch: str, pid: str, *, seed_hex: str | None = None) -> EntryResult:
        return enter(
            self.db.runtime,
            self.store,
            query={
                "PROLIFIC_PID": pid,
                "STUDY_ID": "s",
                "SESSION_ID": "x",
                "batch": batch,
            },
            app_mode="qa",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256=BUILD_SHA,
            seed_hex=seed_hex,
        )

    # -- command dispatch ----------------------------------------------------

    def send(
        self,
        sid: uuid.UUID,
        writer: uuid.UUID,
        kind: str,
        payload: dict[str, Any],
        revision: int,
    ) -> Any:
        """Dispatch one participant command through the real handler path."""
        store = self.store

        def _advance(ctx: Any) -> None:
            bound = lifecycle.bound_for_session(ctx.conn, store, ctx.session)
            from app.services import scheduler

            scheduler.advance_due(
                ctx,
                bound,
                flags=FeatureFlags(
                    time_pressure=self.time_pressure, bonus_payment=self.bonus_payment
                ),
                store=store,
            )

        if kind == "complete":
            # ``complete`` is dispatched via its own handler factory (api/participant.py
            # does the same) — it is not in handler_for's command map.
            handler = lifecycle.completion_handler(
                store=store,
                writer=writer,
                bonus_payment_enabled=self.bonus_payment,
            )
        else:
            handler = lifecycle.handler_for(
                kind,
                store=store,
                behavior_sha=BUILD_SHA,
                writer=writer,
                flags=FeatureFlags(
                    time_pressure=self.time_pressure,
                    ai_qa=self.ai_qa,
                    bonus_payment=self.bonus_payment,
                ),
            )
        return execute_command(
            self.db.runtime,
            Command(
                command_id=uuid.uuid4(),
                session_id=sid,
                kind=kind,
                expected_revision=revision,
                payload=payload,
            ),
            handler,
            advance_due=_advance,
        )

    def _revision(self, sid: uuid.UUID) -> int:
        with self.db.runtime.connect() as conn:
            return int(
                conn.execute(
                    select(s.session.c.revision).where(s.session.c.session_id == sid)
                ).scalar_one()
            )

    # -- stage-spine driver ---------------------------------------------------

    def _drive_to_task(
        self,
        sid: uuid.UUID,
        writer: uuid.UUID,
        bound: BoundManifest,
        report: SessionReport,
        persona: Persona | None = None,
    ) -> None:
        consent_art = lifecycle.effective_consent_artifact(
            bound, self.store, bonus_payment_enabled=self.bonus_payment
        )
        assert consent_art is not None
        consent_key = consent_art.key
        self._dispatch(
            sid,
            writer,
            report,
            Action(
                "consent",
                {"action": "consented", "text_version": consent_key},
            ),
        )
        self._dispatch(sid, writer, report, Action("training_step", {"step": 1}))
        # BA-95: the server requires a committed practice share before completion —
        # the harness performs the same action the UI requires.
        self._dispatch(
            sid,
            writer,
            report,
            Action(
                "training_step",
                {
                    "step": "practice",
                    "action": "share",
                    "record_id": "PRACTICE-K3-04",
                },
            ),
        )
        # v14 (2026-10-16): the server also requires a committed practice
        # answer-entry pick — the DV mechanic rehearsal.
        self._dispatch(
            sid,
            writer,
            report,
            Action(
                "training_step",
                {
                    "step": "practice",
                    "action": "pick_location",
                    "crate": 3,
                    "location": "The Sawmill",
                },
            ),
        )
        # 2026-10-10 (AID-59): nl_only profiles also gate completion on a typed
        # practice ask — the harness sends one whenever the profile's
        # interaction mode requires it.
        if bound.manifest.interaction_mode == "nl_only":
            self._dispatch(
                sid,
                writer,
                report,
                Action(
                    "training_step",
                    {
                        "step": "practice",
                        "action": "ask",
                        "text": "anything on crate 3?",
                    },
                ),
            )
        # 2026-10-15 audit W05: submit the *effective* comprehension key — the
        # same condition-aware map `_training_step` scores against. The harness
        # dispatches through `handler_for` defaults (ai_assistant_enabled=True),
        # so mirror that flag state here; `same_crates` flips to True only under
        # the bound manifest's full-overlap task shape.
        answers = lifecycle.comprehension_key(
            ai_assistant=True, overlap=bound.manifest.task.overlap
        )
        self._dispatch(
            sid,
            writer,
            report,
            Action(
                "training_step",
                {
                    "step": "comprehension",
                    "complete": True,
                    "answers": dict(answers),
                },
            ),
        )
        # The truthful key must advance the session — a miss leaves it stuck in
        # training and the rest of the run silently exercises nothing.
        with self.db.runtime.connect() as conn:
            stage_after = conn.execute(
                select(s.session.c.stage).where(s.session.c.session_id == sid)
            ).scalar_one()
        if stage_after == "training":
            raise RuntimeError(
                f"comprehension did not advance {bound.manifest_id}: stage still "
                f"'training' after submitting {answers}"
            )
        if stage_after == "ai_qa":
            # AI_QA_ENABLED=1: run the persona's Q&A hook (optional questions —
            # default asks nothing), then the always-active Continue.
            if persona is not None:
                view = participant_view(self.db.runtime, self.store, sid, bound)
                for action in persona.on_ai_qa(view, bound):
                    if action.kind != "_nop":
                        self._dispatch(sid, writer, report, action)
            self._dispatch(sid, writer, report, Action("proceed", {}))
        # connecting: PM joins on the wall-clock connecting job
        clockwork.force_wall_due(self.db.runtime, sid, "connecting")
        self._tick()
        self._dispatch(sid, writer, report, Action("ready", {}))
        clockwork.force_wall_due(self.db.runtime, sid, "task_open")
        self._tick()

    # -- the loop ------------------------------------------------------------

    def run_session(
        self,
        manifest_id: str,
        batch_id: str,
        persona: Persona,
        *,
        pid: str | None = None,
        seed: int = 0,
    ) -> SessionReport:
        # BA-84: the world draw derives from the session seed so a run replays the
        # same world — enrollment uses the test-only seed_hex hook (live-refused).
        seed_hex = hashlib.sha256(f"agent-sim:{manifest_id}:{seed}".encode()).hexdigest()
        result = self.enroll(batch_id, pid or f"sim-{uuid.uuid4().hex[:8]}", seed_hex=seed_hex)
        sid, writer = result.session_id, result.writer_id
        bound = bind(self.store, manifest_id, mode="qa")

        report = SessionReport(
            session_id=str(sid),
            persona=persona.name,
            seed=seed,
            manifest_id=manifest_id,
            arm=None,
            stage="enrolled",
            terminal_reason=None,
            active_ms=0,
        )

        self._drive_to_task(sid, writer, bound, report, persona)

        self._wall: dict[uuid.UUID, float] = {}  # job_id -> virtual ms remaining
        wall_ms = 0
        for step_n in range(1, self.max_steps + 1):
            view = participant_view(self.db.runtime, self.store, sid, bound)
            report.arm = view.session_arm
            if view.done:
                report.stage = view.stage
                break

            # 1. wall time accrues; due wall jobs fire (check-in cap, reveal, sweeps)
            wall_ms += self.wall_step_ms
            self._fire_due_wall(sid, self.wall_step_ms)
            self._tick()

            # 2. active task time advances (no-op while paused, e.g. mid check-in)
            if view.stage in ("task", "solo_review"):
                clockwork.advance_active(self.db.runtime, sid, persona.step_ms)
            self._tick()

            # 3. persona sees fresh state and decides
            view = participant_view(self.db.runtime, self.store, sid, bound)
            step = Step(
                n=step_n,
                stage=view.stage,
                active_ms=view.active_ms,
                wall_ms=wall_ms,
            )
            if view.done:
                report.steps.append(step)
                break
            for action in persona.decide(view, bound):
                if action.kind == "_nop":
                    continue
                if action.kind == "_force_wall":
                    clockwork.force_wall_due(self.db.runtime, sid, action.payload["job"])
                    self._tick()
                    continue
                outcome = self._dispatch(sid, writer, report, action)
                step.actions.append(
                    {
                        "kind": action.kind,
                        "payload": action.payload,
                        "note": action.note,
                        "accepted": bool(outcome.accepted),
                        "reason": getattr(outcome, "reason", None),
                    }
                )
                # BA-81: an action marked expect_reject that *succeeded* is a
                # defect signal — surface it as a fail finding, not just a receipt.
                if (f := _expect_reject_finding(action, outcome, step.stage)) is not None:
                    report.findings.append(f)
            self._tick()
            report.steps.append(step)
        else:
            report.findings.append(
                Finding("RUNNER", "fail", f"max_steps {self.max_steps} exhausted", {})
            )

        # terminal accounting
        with self.db.runtime.connect() as conn:
            row = conn.execute(
                select(s.session.c.stage, s.session.c.terminal_reason).where(
                    s.session.c.session_id == sid
                )
            ).one()
            report.stage, report.terminal_reason = row
        report.active_ms = clockwork.active(self.db.runtime, sid)
        report.jobs = self._job_dump(sid)
        report.findings.extend(check_session(self.db.runtime, sid, persona.name, bound))
        try:
            from app.services import adminviews

            report.replay = adminviews.replay(
                self.db.runtime,
                sid,
                include_restricted=True,
                flags=FeatureFlags(time_pressure=self.time_pressure),
            )
            if report.replay is not None:
                # Lets the standalone simulation label the bot strategy in its
                # banner — live replays carry no such field.
                report.replay["persona"] = persona.name
        except (SQLAlchemyError, KeyError, TypeError, ValueError) as exc:
            # The transcript/oracles are the primary evidence — a capture failure
            # warns instead of failing the run.
            report.replay = None
            report.findings.append(Finding("RUNNER", "warn", f"replay capture failed: {exc!r}", {}))
        return report

    def _job_dump(self, sid: uuid.UUID) -> list[dict[str, Any]]:
        """Final scheduled-job state — evidence for why wall/active jobs did or
        didn't fire (kind, basis, status, outcome) plus remaining virtual budget."""
        with self.db.runtime.connect() as conn:
            return [
                {
                    "kind": r.kind,
                    "basis": r.clock_basis,
                    "status": r.status,
                    "outcome": r.outcome_reason,
                    "due_active_ms": r.due_active_ms,
                    "wall_ms_left": round(self._wall.get(r.job_id, -1)),
                }
                for r in conn.execute(
                    select(
                        s.scheduled_job.c.job_id,
                        s.scheduled_job.c.kind,
                        s.scheduled_job.c.clock_basis,
                        s.scheduled_job.c.status,
                        s.scheduled_job.c.outcome_reason,
                        s.scheduled_job.c.due_active_ms,
                    )
                    .where(s.scheduled_job.c.session_id == sid)
                    .order_by(s.scheduled_job.c.created_at)
                )
            ]

    # -- internals -------------------------------------------------------------

    def _tick(self) -> int:
        return clockwork.tick(
            self.db.runtime,
            self.store,
            provider=self.provider,
            worker=self.worker,
            flags=FeatureFlags(time_pressure=self.time_pressure, bonus_payment=self.bonus_payment),
        )

    def _fire_due_wall(self, sid: uuid.UUID, step_ms: int) -> int:
        """Count down each pending wall-basis job by ``step_ms`` of virtual wall time
        and make claimable any whose remaining delay reaches zero.

        Each job's budget is its own ``due_wall_at - created_at`` delay measured at
        first sight, so a 120 s check-in cap only fires after 120 s of *virtual*
        wall time has elapsed *since the check-in opened* — not since the run
        started. Returns the number of jobs newly made due."""
        with self.db.runtime.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT job_id, due_wall_at, created_at FROM app.scheduled_job "
                    "WHERE session_id = :sid AND status = 'pending' "
                    "AND clock_basis = 'wall' AND due_wall_at IS NOT NULL"
                ),
                {"sid": sid},
            ).all()
            live = {r.job_id for r in rows}
            for dead in [j for j in self._wall if j not in live]:
                del self._wall[dead]
            due: list[uuid.UUID] = []
            for r in rows:
                if r.job_id not in self._wall:
                    self._wall[r.job_id] = (r.due_wall_at - r.created_at).total_seconds() * 1000
                self._wall[r.job_id] -= step_ms
                if self._wall[r.job_id] <= 0:
                    due.append(r.job_id)
            if due:
                # Both halves of the due clause must hold: claimable_at <= now AND
                # (for wall jobs) due_wall_at <= now.
                conn.execute(
                    text(
                        "UPDATE app.scheduled_job "
                        "SET claimable_at = clock_timestamp(), due_wall_at = clock_timestamp() "
                        "WHERE job_id = ANY(:ids)"
                    ),
                    {"ids": due},
                )
            return len(due)

    def _dispatch(
        self,
        sid: uuid.UUID,
        writer: uuid.UUID,
        report: SessionReport,
        action: Action,
    ) -> Any:
        revision = self._revision(sid) + action.revision_offset
        outcome = self.send(sid, writer, action.kind, action.payload, revision)
        report.transcript.append(
            {
                "kind": action.kind,
                "payload": action.payload,
                "note": action.note,
                "revision": revision,
                "accepted": bool(outcome.accepted),
                "reason": getattr(outcome, "reason", None),
            }
        )
        return outcome
