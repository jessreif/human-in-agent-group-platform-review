/**
 * Check-in overlay (INV-0804 stage locks; the TP check-in is server-driven): an
 * accessible modal over the workbench. Phases: collect (own rating) → wait (server's
 * 3.5 s collection presentation) → reveal (teammate values + explicit resume). Focus is
 * moved into the dialog on open and returned on close; ESC is intentionally not bound —
 * the check-in is a required interaction, not a dismissible popup.
 */
import { useEffect, useRef, useState } from "react";

import { displaySeat, youLabel } from "../state/names";
import { canWrite, type SessionStore, type StoreState } from "../state/store";

const SCALE = [1, 2, 3, 4, 5, 6, 7];
// SQ-7: the stated response window is a group norm, not the technical cap — the
// server's inactivity kill fires at a longer wall budget, so the stated 60 s
// under-promises safely (a longer stated deadline would lie).
const STATED_SECONDS = 60;

export function CheckinOverlay({
  state,
  store,
}: {
  state: StoreState;
  store: SessionStore;
}) {
  const checkin = state.checkin;
  const boxRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<Element | null>(null);
  const [busy, setBusy] = useState(false);

  // TIME_PRESSURE_ENABLED=0: the check-in job is never scheduled, so the
  // overlay can never legitimately open — the flag guard is belt-and-braces.
  const open = checkin.phase !== "closed" && state.timePressure;
  const [remaining, setRemaining] = useState(STATED_SECONDS);

  useEffect(() => {
    if (!open || checkin.openedAtMs == null) return;
    const tick = () =>
      setRemaining(
        Math.max(0, STATED_SECONDS - Math.floor((Date.now() - checkin.openedAtMs!) / 1000)),
      );
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [open, checkin.openedAtMs]);

  useEffect(() => {
    if (open && restoreRef.current === null) {
      restoreRef.current = document.activeElement;
      // Focus the dialog container, not a control: a check-in can open while
      // the participant is typing in chat, and a focused rating button turns
      // the next spacebar press into a submitted answer (observed 2026-10-05).
      boxRef.current?.focus();
    } else if (!open && restoreRef.current) {
      const el = restoreRef.current;
      restoreRef.current = null;
      if (el instanceof HTMLElement) el.focus();
    }
  }, [open]);

  // Simple containment: keep Tab inside the dialog while it is open.
  function onKeyDown(e: React.KeyboardEvent) {
    // Mouse-only submissions (RL 2026-10-05): a check-in can open while the
    // participant is typing in chat — a stray Enter/Space reaching a control
    // must never select a rating or resume. Enter activates on keydown and
    // Space arms on keydown (fires on keyup) — canceling keydown stops both.
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      return;
    }
    if (e.key !== "Tab" || !boxRef.current) return;
    const items = boxRef.current.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    if (items.length === 0) return;
    const first = items[0] as HTMLElement;
    const last = items[items.length - 1] as HTMLElement;
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  if (!open) return null;
  const copy = checkin.copy;

  return (
    <div className="overlay" data-testid="checkin-overlay">
      <div
        className="overlay-box"
        role="dialog"
        aria-modal="true"
        aria-label={copy?.reveal_title ?? "Team check-in"}
        ref={boxRef}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <h2>Brief team check-in</h2>
        <p className="overlay-sub">The task clock is paused.</p>
        {/* SQ-7: the response norm is stated everywhere the overlay is — a
            participant who walks away should know the window exists. */}
        <p className="checkin-deadline" role="status">
          {remaining > 0
            ? `All members should respond within ${remaining} seconds so the group task can continue.`
            : "All members need to respond so the group task can continue."}
        </p>

        {checkin.phase === "collect" ? (
          <>
            <p>{copy?.question ?? "How heavy does your current workload feel right now?"}</p>
            <div className="likert-row" role="radiogroup" aria-label="Your rating">
              {SCALE.map((n) => (
                <button
                  key={n}
                  type="button"
                  disabled={busy || checkin.submitStatus === "saving" || !canWrite(state)}
                  onClick={() => {
                    setBusy(true);
                    void store
                      .submitCheckin(n)
                      .catch(() => undefined)
                      .finally(() => setBusy(false));
                  }}
                >
                  {n}
                </button>
              ))}
            </div>
            <div className="scale-ends">
              <span>{copy?.scale_min_label ?? "Low"}</span>
              <span>{copy?.scale_max_label ?? "High"}</span>
            </div>
            <p className="overlay-note">{copy?.note ?? ""}</p>
            {checkin.submitStatus === "rejected" ? (
              <p className="error-note" role="alert">
                Your rating was not saved — please try again.
              </p>
            ) : null}
            {/* UX round 3, item 5: no skip control — a rating is required. */}
          </>
        ) : null}

        {checkin.phase === "wait" ? (
          <p role="status">{copy?.collecting ?? "Collecting your teammates' ratings…"}</p>
        ) : null}

        {checkin.phase === "reveal" ? (
          <>
            <h3>{copy?.reveal_title ?? "Ratings reported"}</h3>
            <div className="reveal-row">
              {/* UX round 4, item 12: ratings read as N/7 — the 7-point scale is
                  part of the manipulation the reveal should make legible. */}
              {(checkin.assignedValues ? Object.entries(checkin.assignedValues) : [])
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([seat, v]) => (
                  <span key={seat} className="reveal-chip">
                    {displaySeat(seat, state.initials)}: <b>{v}/7</b>
                  </span>
                ))}
              <span className="reveal-chip">
                {youLabel(state.initials)}: <b>{checkin.ownValue ?? "—"}/7</b>
              </span>
            </div>
            <p className="overlay-note">{copy?.reveal_note ?? ""}</p>
            <button
              type="button"
              className="primary"
              disabled={busy || !canWrite(state)}
              onClick={() => {
                setBusy(true);
                void store
                  .proceed()
                  .catch(() => undefined)
                  .finally(() => setBusy(false));
              }}
            >
              {copy?.resume_label ?? "Resume task"}
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}
