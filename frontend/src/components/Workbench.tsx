/**
 * The simultaneous workbench (INV-0802): case file, team chat and AI assistant visible
 * at once — no exclusive tabs — with the four assigned-crate answer controls in a
 * persistent bottom bar (ascending crate order) and the countdown/status strip on top.
 * The check-in overlay sits above the grid when the server opens one.
 *
 * Draft discipline (INV-0806): each picker reflects the store's AnswerEntry; "saving"
 * and "saved" come from real command outcomes only, and a rejected late answer keeps its
 * rejection marker rather than claiming a save.
 */
import { useEffect, useReducer, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { remainingMs } from "../state/clock";
import { CaseFile } from "./CaseFile";
import { ChatPanel } from "./ChatPanel";
import { AssistantPanel } from "./AssistantPanel";
import { CheckinOverlay } from "./CheckinOverlay";
import { Countdown } from "./Countdown";
import { surfaceText } from "./StageScreens";
import { canWrite, type SessionStore, type StoreState } from "../state/store";
import type { TelemetryQueue } from "../telemetry";
import type { AnswerEntry } from "../state/store";

const SPLIT_MIN = 0.25;
const SPLIT_MAX = 0.75;
const clampSplit = (v: number) =>
  Math.round(Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, v)) * 1000) / 1000;

const STATUS_LABEL: Record<AnswerEntry["status"], string> = {
  clean: "",
  dirty: "Unsaved changes",
  saving: "Saving…",
  saved: "Saved",
  rejected: "Not saved",
};

/** A crate counts as entered once the server has acknowledged a value —
 * drafts and rejected writes do not fill a pill (the v13 zero-answer cohort
 * froze empty boards: only committed state is real). */
function entered(state: StoreState, crate: number): boolean {
  return (state.answers[crate]?.committed ?? "") !== "";
}

/**
 * v14 (2026-10-16, owner direction): a persistent neutral progress state in
 * the answer bar — "N of M crates recorded" plus per-crate pills that fill as
 * committed picks land. Informational only; no urgency styling, so the
 * nopress contract (no warnings) is untouched.
 */
function AnswerProgress({ state, assigned }: { state: StoreState; assigned: number[] }) {
  const n = assigned.filter((c) => entered(state, c)).length;
  return (
    <span className="answer-progress" aria-live="polite">
      <span className="progress-count" data-surface="answer-progress">
        {n} of {assigned.length} crates recorded
      </span>
      <span className="crate-pills">
        {assigned.map((c) => (
          <span
            key={c}
            className={entered(state, c) ? "crate-pill entered" : "crate-pill"}
            aria-label={entered(state, c) ? `Crate ${c}: entered` : `Crate ${c}: not entered`}
          >
            {c}
          </span>
        ))}
      </span>
    </span>
  );
}

/**
 * v14 (2026-10-16, owner direction): a final-check notice at T-15s — shown to
 * every participant, not just empty boards ("Please double-check…"). Factual,
 * not a warning — the nopress contract (no low-time cues) is untouched. Ticks
 * locally; the deadline itself still comes from server events.
 */
const FINAL_CHECK_NOTICE_MS = 15_000;

function FinalCheckNotice({ state, locked }: { state: StoreState; locked: boolean }) {
  const [, tick] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    const id = setInterval(tick, 500);
    return () => clearInterval(id);
  }, []);
  const remaining = remainingMs(state.clock, Date.now());
  if (
    locked ||
    remaining === null ||
    remaining > FINAL_CHECK_NOTICE_MS ||
    remaining <= 0
  ) {
    return null;
  }
  return (
    <span className="final-check-note" role="status" data-surface="final-check">
      Please double-check your crate locations now.
    </span>
  );
}

function CratePicker({
  crate,
  item,
  entry,
  locations,
  locked,
  onChange,
  onBlurFlush,
}: {
  crate: number;
  item: string | undefined;
  entry: AnswerEntry | undefined;
  locations: string[];
  locked: boolean;
  onChange: (value: string) => void;
  onBlurFlush: () => void;
}) {
  const value = entry?.value ?? "";
  const status: AnswerEntry["status"] = entry?.status ?? "clean";
  const error = entry?.error;
  const frozen = locked || error === "closed";
  return (
    <div className="crate-picker" data-crate={crate} data-status={status}>
      <label htmlFor={`crate-${crate}`}>
        Crate {crate}
        {item ? <span className="crate-item"> — {item}</span> : null}
      </label>
      <select
        id={`crate-${crate}`}
        value={value}
        disabled={frozen}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlurFlush}
      >
        <option value="">— choose a location —</option>
        {locations.map((loc) => (
          <option key={loc} value={loc}>
            {loc}
          </option>
        ))}
      </select>
      <span
        className={`save-status save-${status}`}
        role={status === "rejected" || status === "dirty" ? "status" : undefined}
      >
        {error === "closed" ? "Not saved — task closed" : STATUS_LABEL[status]}
      </span>
    </div>
  );
}

export function Workbench({
  state,
  store,
  telemetry,
}: {
  state: StoreState;
  store: SessionStore;
  telemetry: TelemetryQueue;
}) {
  // BA-26: the surface is writable only while this tab holds a confirmed lease —
  // "unknown" (boot resume still unresolved) is locked exactly like "readonly".
  const unwritable = !canWrite(state);
  // While a check-in overlay is open the underlying surface stays inert — the overlay
  // blocks pointer events, but the disabled controls keep keyboard focus honest too.
  const checkinOpen = state.checkin.phase !== "closed";
  const controlsLocked = unwritable || state.taskClosed || checkinOpen;
  const assigned = [...state.assignedCrates].sort((a, b) => a - b);
  // AI_ASSISTANT_ENABLED=0: no assistant pane or splitter — team chat fills the
  // whole right column (CSS handles the row span).
  const aiAssistant = state.aiAssistant;
  // 2026-10-10 revision added a neutral "Entry on Crate N added to your file"
  // cue beside pickers whose file gained a teammate-delivered entry. Owner
  // direction 2026-10-11 removes it for the next version: bottom-ribbon clutter
  // and a possible perceived nudge to revise answers from team intel, which
  // would confound the information-source comparison.

  // Resizable workbench: --sx splits case-file | right column, --sy splits
  // team-chat | assistant inside the right column. Session-only (no
  // persistence) so a lab machine never leaks layout across participants.
  const gridRef = useRef<HTMLDivElement>(null);
  const submitBarRef = useRef<HTMLElement>(null);
  const [sx, setSx] = useState(0.45);
  const [sy, setSy] = useState(0.55);

  // 2026-10-18 answer-bar visibility (zero-answer cohort hypothesis): on
  // <900px viewports the submit bar is in-flow and can sit below the fold —
  // emit its IntersectionObserver bucket + viewport geometry, no content.
  useEffect(() => {
    const el = submitBarRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    let lastBucket: string | null = null;
    const obs = new IntersectionObserver(
      (entries) => {
        const ratio = entries[0]?.intersectionRatio ?? 0;
        const bucket = ratio >= 0.9 ? "full" : ratio >= 0.5 ? "half" : ratio > 0 ? "sliver" : "hidden";
        if (bucket === lastBucket) return;
        lastBucket = bucket;
        telemetry.emit("submit_bar_view", {
          bucket,
          ratio: Math.round(ratio * 100) / 100,
          viewport_w: window.innerWidth,
          viewport_h: window.innerHeight,
          page_height: document.documentElement.scrollHeight,
          dpr: window.devicePixelRatio,
        });
      },
      { threshold: [0, 0.25, 0.5, 0.75, 0.9] },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [telemetry]);

  const startDrag =
    (axis: "x" | "y") => (e: PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      const el = e.currentTarget;
      // BA-77: the drag belongs to the pointer that started it — a second touch
      // on the same handle must not move the split or end the first drag.
      const pid = e.pointerId;
      if (typeof el.setPointerCapture === "function" && pid !== undefined) {
        el.setPointerCapture(pid);
      }
      const move = (ev: globalThis.PointerEvent) => {
        if (ev.pointerId !== pid) return;
        const r = gridRef.current?.getBoundingClientRect();
        if (!r) return;
        if (axis === "x") {
          setSx(clampSplit((ev.clientX - r.left - 10) / (r.width - 30)));
        } else {
          setSy(clampSplit((ev.clientY - r.top - 10) / (r.height - 30)));
        }
      };
      const up = (ev: globalThis.PointerEvent) => {
        if (ev.pointerId !== pid) return;
        if (typeof el.releasePointerCapture === "function") {
          try {
            el.releasePointerCapture(pid);
          } catch {
            // capture already released (cancel/unmount)
          }
        }
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", up);
        el.removeEventListener("pointercancel", up);
      };
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
    };

  const onSeparatorKey =
    (axis: "x" | "y") => (e: KeyboardEvent<HTMLDivElement>) => {
      const step = e.shiftKey ? 0.1 : 0.05;
      const set = axis === "x" ? setSx : setSy;
      const dec = axis === "x" ? "ArrowLeft" : "ArrowUp";
      const inc = axis === "x" ? "ArrowRight" : "ArrowDown";
      if (e.key === dec) {
        e.preventDefault();
        set((v) => clampSplit(v - step));
      } else if (e.key === inc) {
        e.preventDefault();
        set((v) => clampSplit(v + step));
      } else if (e.key === "Home") {
        e.preventDefault();
        set(SPLIT_MIN);
      } else if (e.key === "End") {
        e.preventDefault();
        set(SPLIT_MAX);
      }
    };

  return (
    <div className="task-screen" data-stage={state.stage}>
      <header className="task-header">
        <div className="task-heading">
          <b>Case file</b>
          <span className="goal">Locate your crates, then record your answers at bottom of page</span>
        </div>
        {/* v14 (2026-10-16, owner direction): the answer progress lives in the
            top ribbon, left of the timer — the footer copy was buried; the
            pill row gives the same feedback loop as share confirmations. */}
        <span className="task-center">
          <span className="task-progress">
            <AnswerProgress state={state} assigned={assigned} />
            <FinalCheckNotice state={state} locked={controlsLocked} />
          </span>
          <Countdown state={state} />
        </span>
        <span className="mine-strip">
          Your crates: {assigned.length ? assigned.join(", ") : "…"}
        </span>
      </header>

      {/* INV-0903: review-first solo review — help channels are locked until the
          check-in hands off to the task phase; file work + drafting stay enabled. */}
      {state.stage === "solo_review" ? (
        <div className="notice-bar" role="status" data-surface="solo_review">
          {aiAssistant
            ? "Review your case file independently for one minute — the team chat and AI assistant open when five minutes remain. You can already draft answers below."
            : "Review your case file independently for one minute — the team chat opens when five minutes remain. You can already draft answers below."}
        </div>
      ) : null}

      {/* INV-0904: the bound PE exposure strip (eval arm only — the control copy is
          empty, so this block is simply absent for controls). */}
      {state.exposure?.strip ? (
        <div className="exposure-strip" role="note" data-surface="strip">
          {surfaceText(state.exposure.strip)}
        </div>
      ) : null}

      {state.leaseNotice || state.writer === "readonly" || state.authFailed ? (
        <div className="notice-bar" role="alert">
          This session may be open in another window — this window is read-only.
          <button type="button" onClick={() => void store.takeover().catch(() => undefined)}>
            Take over here
          </button>
        </div>
      ) : state.writer === "unknown" ? (
        // Neutral pending treatment while the boot resume is unresolved — same
        // notice-bar styling, a status (not alert) role, and no new copy.
        <div className="notice-bar" role="status">
          Preparing your session…
        </div>
      ) : null}

      <div
        ref={gridRef}
        className={aiAssistant ? "task-grid" : "task-grid no-assistant"}
        aria-hidden={checkinOpen}
        style={{ "--sx": sx, "--sy": sy } as CSSProperties}
      >
        <CaseFile
          state={state}
          store={store}
          telemetry={telemetry}
          disabled={unwritable || checkinOpen}
          // Check-in overlay genuinely occludes the file — distinct from
          // disabled (unwritable but visible). Exposure telemetry suspends.
          occluded={checkinOpen}
        />
        <ChatPanel
          state={state}
          store={store}
          telemetry={telemetry}
          disabled={unwritable || checkinOpen}
        />
        {aiAssistant ? (
          <AssistantPanel
            state={state}
            store={store}
            telemetry={telemetry}
            disabled={unwritable || checkinOpen}
          />
        ) : null}
        <div
          className="grid-resizer grid-resizer-x"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize case file and chat panels"
          aria-valuenow={Math.round(sx * 100)}
          aria-valuemin={SPLIT_MIN * 100}
          aria-valuemax={SPLIT_MAX * 100}
          tabIndex={0}
          onPointerDown={startDrag("x")}
          onKeyDown={onSeparatorKey("x")}
        />
        {aiAssistant ? (
          <div
            className="grid-resizer grid-resizer-y"
            role="separator"
            aria-orientation="horizontal"
            aria-label="Resize team chat and assistant panels"
            aria-valuenow={Math.round(sy * 100)}
            aria-valuemin={SPLIT_MIN * 100}
            aria-valuemax={SPLIT_MAX * 100}
            tabIndex={0}
            onPointerDown={startDrag("y")}
            onKeyDown={onSeparatorKey("y")}
          />
        ) : null}
      </div>

      <footer className="submit-bar" aria-label="Your answers" ref={submitBarRef}>
        {/* AB-20: one compact head block beside the pickers — the stacked
            title+prompt+row used to cost the panes ~50 px of viewport. */}
        <div className="submit-head">
          <h2 className="submit-title">Your answers</h2>
          <p className="prompt">
            Record where each of your crates is currently located. Any crate location
            selected before the clock runs out is your final answer.{" "}
            {state.bonusPayment ? (
              <b>
                You are eligible for a bonus based on how many locations you correctly identify.
              </b>
            ) : null}
          </p>
        </div>
        <div className="submit-row">
          {assigned.map((crate) => (
            <CratePicker
              key={crate}
              crate={crate}
              item={state.crateItems[crate]}
              entry={state.answers[crate]}
              locations={state.locations}
              locked={controlsLocked}
              onChange={(v) => store.setAnswerDraft(crate, v)}
              onBlurFlush={() => void store.flushAnswer(crate)}
            />
          ))}
        </div>
      </footer>

      <CheckinOverlay state={state} store={store} />
    </div>
  );
}
